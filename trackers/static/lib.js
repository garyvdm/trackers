"use strict";

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
