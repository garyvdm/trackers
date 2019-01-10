"use strict";

var date_locale = 'en-GB';
var date_options = {weekday: 'short',  day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' };
var date_options_delta = {weekday: 'short', hour: '2-digit', minute: '2-digit' };
var date_options_full = {weekday: 'short',  day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' };


function format_time_delta(seconds, show_days) {
    if (show_days) {
        return sprintf('%id %02i:%02i:%02i',
            Math.floor(seconds / 60 / 60 / 24), /* days */
            Math.floor(seconds / 60 / 60 % 24), /* hours */
            Math.floor(seconds / 60 % 60),      /* min */
            Math.floor(seconds % 60)            /* seconds */
            );
    } else {
        return sprintf('%02i:%02i:%02i',
            Math.floor(seconds / 60 / 60),      /* hours */
            Math.floor(seconds / 60 % 60),      /* min */
            Math.floor(seconds % 60)            /* seconds */
            );
    }
}


function format_time_delta_ago(seconds){
    // TODO more than a day
    if (seconds < 60) { return '< 1 min ago' }
    else if (seconds < 60 * 60) { return sprintf('%i min ago', Math.floor(seconds / 60)) }
    else if (seconds < 60 * 60 * 24) { return sprintf('%i:%02i ago', Math.floor(seconds / 60 / 60), Math.floor(seconds / 60 % 60)) }
    else { return sprintf('%id %i:%02i ago', Math.floor(seconds / 60 / 60 / 24), Math.floor(seconds / 60 / 60 % 24), Math.floor(seconds / 60 % 60))}
}

function format_time_delta_ago_with_date(current_time, time, options){
    var formated = format_time_delta_ago(current_time - time);
    if (current_time - time > 60 * 60 * 4) {
        formated += sprintf('<br>%s', new Date(time  * 1000).toLocaleString(date_locale, options));
    }
    return formated
}

function process_update_list(fetch_block, old_list, update){
    return new Promise(function (resolve, reject) {
        if (update.hasOwnProperty('blocks') || update.hasOwnProperty('partial_block')) {
            var blocks = (update.blocks || [] );

            var partial_block = update.partial_block;

            var fetch_promises = blocks.map(function(block) {
                if (block.end_index >= old_list.length || block.end_hash != old_list[block.end_index].hash) {
                    return fetch_block(block);
                } else {
                    return old_list.slice(block.start_index, block.end_index + 1)
                }
            });
            fetch_promises.push(partial_block);

            Promise.all(fetch_promises).then(function (new_blocks){
                var new_list = [];
                new_blocks.forEach(function (block) { new_list = new_list.concat(block); });

                // Find the first item that differs. Could maybe use a binary search for this.
                var min_length = Math.min(new_list.length, old_list.length)
                for (var first_new_index=0; first_new_index<min_length; first_new_index++){
                    if (new_list[first_new_index].hash != old_list[first_new_index].hash) {
                        break;
                    }
                }
                var old_items = old_list.slice(first_new_index, old_list.length);
                var new_items = new_list.slice(first_new_index, new_list.length);
                resolve({
                    'old_items': old_items,
                    'new_items': new_items,
                    'new_list': new_list,
                });
            });
        } else if (update.hasOwnProperty('add_block'))  {
            resolve({
                'old_items': [],
                'new_items': update.add_block,
                'new_list': old_list.concat(update.add_block)
            });
        } else {
            reject('Unknown update format');
        }
    });
}


function Deferred() {
    /* A method to resolve the associated Promise with the value passed.
     * If the promise is already settled it does nothing.
     *
     * @param {anything} value : This value is used to resolve the promise
     * If the value is a Promise then the associated promise assumes the state
     * of Promise passed as value.
     */
    this.resolve = null;

    /* A method to reject the assocaited Promise with the value passed.
     * If the promise is already settled it does nothing.
     *
     * @param {anything} reason: The reason for the rejection of the Promise.
     * Generally its an Error object. If however a Promise is passed, then the Promise
     * itself will be the reason for rejection no matter the state of the Promise.
     */
    this.reject = null;

    /* A newly created Pomise object.
     * Initially in pending state.
     */
    this.promise = new Promise(function(resolve, reject) {
        this.resolve = resolve;
        this.reject = reject;
    }.bind(this));
    Object.freeze(this);
}

Array.prototype.extend = function (other_array) {
    other_array.forEach(function(v) {this.push(v)}, this);
}

function binary_search_closest(arr, searchElement) {
    var minIndex = 0;
    var maxIndex = arr.length - 1;
    var currentIndex;
    var currentElement;
    var nextElement;

    while (minIndex <= maxIndex) {
        currentIndex = (minIndex + maxIndex) / 2 | 0;
        currentElement = arr[currentIndex];
        nextElement = arr[currentIndex + 1]

        if (currentElement <= searchElement && searchElement < nextElement) {
            return currentIndex;
        }

        if (currentElement < searchElement) {
            minIndex = currentIndex + 1;
        } else if (searchElement < nextElement) {
            maxIndex = currentIndex;
        }

        if (minIndex == maxIndex) {
            console.log(minIndex, maxIndex, currentElement, nextElement, searchElement)
            return;
        }
    }
}


/*! https://mths.be/cssescape v1.5.1 by @mathias | MIT license */
;(function(root, factory) {
	// https://github.com/umdjs/umd/blob/master/returnExports.js
	if (typeof exports == 'object') {
		// For Node.js.
		module.exports = factory(root);
	} else if (typeof define == 'function' && define.amd) {
		// For AMD. Register as an anonymous module.
		define([], factory.bind(root, root));
	} else {
		// For browser globals (not exposing the function separately).
		factory(root);
	}
}(typeof global != 'undefined' ? global : this, function(root) {

	if (root.CSS && root.CSS.escape) {
		return root.CSS.escape;
	}

	// https://drafts.csswg.org/cssom/#serialize-an-identifier
	var cssEscape = function(value) {
		if (arguments.length == 0) {
			throw new TypeError('`CSS.escape` requires an argument.');
		}
		var string = String(value);
		var length = string.length;
		var index = -1;
		var codeUnit;
		var result = '';
		var firstCodeUnit = string.charCodeAt(0);
		while (++index < length) {
			codeUnit = string.charCodeAt(index);
			// Note: there’s no need to special-case astral symbols, surrogate
			// pairs, or lone surrogates.

			// If the character is NULL (U+0000), then the REPLACEMENT CHARACTER
			// (U+FFFD).
			if (codeUnit == 0x0000) {
				result += '\uFFFD';
				continue;
			}

			if (
				// If the character is in the range [\1-\1F] (U+0001 to U+001F) or is
				// U+007F, […]
				(codeUnit >= 0x0001 && codeUnit <= 0x001F) || codeUnit == 0x007F ||
				// If the character is the first character and is in the range [0-9]
				// (U+0030 to U+0039), […]
				(index == 0 && codeUnit >= 0x0030 && codeUnit <= 0x0039) ||
				// If the character is the second character and is in the range [0-9]
				// (U+0030 to U+0039) and the first character is a `-` (U+002D), […]
				(
					index == 1 &&
					codeUnit >= 0x0030 && codeUnit <= 0x0039 &&
					firstCodeUnit == 0x002D
				)
			) {
				// https://drafts.csswg.org/cssom/#escape-a-character-as-code-point
				result += '\\' + codeUnit.toString(16) + ' ';
				continue;
			}

			if (
				// If the character is the first character and is a `-` (U+002D), and
				// there is no second character, […]
				index == 0 &&
				length == 1 &&
				codeUnit == 0x002D
			) {
				result += '\\' + string.charAt(index);
				continue;
			}

			// If the character is not handled by one of the above rules and is
			// greater than or equal to U+0080, is `-` (U+002D) or `_` (U+005F), or
			// is in one of the ranges [0-9] (U+0030 to U+0039), [A-Z] (U+0041 to
			// U+005A), or [a-z] (U+0061 to U+007A), […]
			if (
				codeUnit >= 0x0080 ||
				codeUnit == 0x002D ||
				codeUnit == 0x005F ||
				codeUnit >= 0x0030 && codeUnit <= 0x0039 ||
				codeUnit >= 0x0041 && codeUnit <= 0x005A ||
				codeUnit >= 0x0061 && codeUnit <= 0x007A
			) {
				// the character itself
				result += string.charAt(index);
				continue;
			}

			// Otherwise, the escaped character.
			// https://drafts.csswg.org/cssom/#escape-a-character
			result += '\\' + string.charAt(index);

		}
		return result;
	};

	if (!root.CSS) {
		root.CSS = {};
	}

	root.CSS.escape = cssEscape;
	return cssEscape;

}));
