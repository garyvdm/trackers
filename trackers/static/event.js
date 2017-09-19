"use strict";

var TIME = 't'
var POSITION = 'p'
var TRACK_ID = 'i'
var STATUS = 's'
var DIST_ROUTE = 'o'
var DIST_RIDDEN = 'd'
var HASH = 'h'
var INDEX = 'x'


document.addEventListener('DOMContentLoaded', function() {
    var status = document.getElementById('status');
    var status_msg = '';
    var errors = [];
    var time_offset = 0;

    function update_status(){
        var text = errors.slice(-4).concat([status_msg]).join('<br>');
//        console.log(text);
        status.innerHTML = text;
    }

    function set_status(status){
        status_msg = status;
        update_status();
    }

    window.onerror = function (messageOrEvent, source, lineno, colno, error){{
        errors.push(messageOrEvent);
        update_status();
        var full_error_message = messageOrEvent + '\n' + (error? error.stack: source + ':' + lineno + ':' + colno)
        setTimeout(function () {{
            var request = new XMLHttpRequest();
            request.open("POST", '/client_error', true);
            request.send(full_error_message);
        }}, 100);
        return false;
    }}

    var main_el = document.getElementById('main');
    var mobile_selectors = document.getElementById('mobile_select').querySelectorAll('div');
    var mobile_selected;

    function apply_mobile_selected(selected){
        mobile_selected = selected;
        main_el.className = 'show_' + selected;
        Array.prototype.forEach.call(mobile_selectors, function (el){
            el.className = (el.getAttribute('show') == selected?'selected':'')
        });
        if (selected=='map') google.maps.event.trigger(map, 'resize');
    }
    Array.prototype.forEach.call(mobile_selectors, function (el){
        var el_selects = el.getAttribute('show')
        el.onclick = function(){apply_mobile_selected(el_selects);};
    });

    var map = new google.maps.Map(document.getElementById('map'), {
        center: {lat: 0, lng: 0},
        zoom: 2,
        mapTypeId: 'terrain',
        mapTypeControl: true,
        mapTypeControlOptions: {
            position: google.maps.ControlPosition.TOP_RIGHT
        }
    });
    apply_mobile_selected('map');

    var ws;
    var close_reason;
    var reconnect_time = 1000;

    var loader_html = '<span class="l1"></span><span class="l2"></span><span class="l3"></span> '

    function get_initial_state(){
        http_get('initial_data', function (new_state){
            state = new_state;
            save_state();
            on_state_loaded();
        });
    }

    function save_state(){
        if (state.live) {
            window.localStorage.setItem(location.pathname, JSON.stringify(state));
        } else {
            window.localStorage.removeItem(location.pathname);
        }
    }

    function on_state_loaded() {
        try{
            event_data = state.event_data
            on_new_event_data();
            on_new_routes_hash();
            Object.keys(state.riders_points).forEach(function(name) {
                var update = state.riders_points[name];
                state.riders_points[name] = {'full_blocks': [], 'partial_block': []};
                if (update.empty) {
                    update.full_blocks = [];
                    update.partial_block = [];
                    delete update.empty;
                }
                var rider_points = riders_points[name] || (riders_points[name] = []);
                load_update_list('rider_points?name=' + name,
                                 state.riders_points[name], update, rider_points,
                                 on_new_rider_points.bind(null, name));
            });
        }
        finally {
            if (state.live) {
                setTimeout(ws_connect, 0);
            } else {
                set_status('');
            }
        }
    }

    function on_new_routes_hash(){
        http_get('routes?hash=' + state.routes_hash, function (new_routes){
            routes = new_routes;
            on_new_routes();
        });
    }

    function http_get(url, load){
        var req = new XMLHttpRequest();
        req.addEventListener("load", function(){
            if (req.status == 200) {
                load(JSON.parse(req.responseText));
            } else {
                errors.push(req.responseText);
                update_status();
            }
        });
        req.open("GET", location.pathname + '/' + url);
        req.send();
    }

    function load_update_list(url, state, update, list, on_loaded){

        var full_blocks = update.full_blocks || [];
        var partial_block = update.partial_block || [];

        if (!full_blocks.length && ! partial_block.length && !update.empty) return; // if no changes, don't do anything.

        var start_index;
        var end_index;

        if (update.empty) {
            start_index = [];
            end_index = [];
            state.full_blocks = [];
            state.partial_block = [];
        } else {
            start_index = ( full_blocks.length ? full_blocks[0].start_index : partial_block[0][INDEX] )
            end_index = ( partial_block.length ? partial_block[partial_block.length - 1][INDEX] : full_blocks[full_blocks.length - 1].end_index )

            state.full_blocks = state.full_blocks.filter(function (block) {return block.end_index < start_index});
            state.partial_block = state.partial_block.filter(function (item) {return item[INDEX] < start_index});

            state.full_blocks.extend(full_blocks);
            state.partial_block.extend(partial_block);
        }

        var old_items = list.splice(start_index, list.length);
        list.length = end_index + 1

        var blocks_to_load = ( full_blocks ? full_blocks.length : 0 )

        function on_block_loaded() {
            // TODO show partially loaded data.
            if (blocks_to_load == 0) {
                on_loaded(start_index, old_items)
            }
        }

        partial_block.forEach(function (item) {
            list[item[INDEX]] = item
        });
        full_blocks.forEach(function (block) {
            var block_url = url + '&start_index=' + block.start_index + '&end_index=' + block.end_index + '&end_hash=' + block.end_hash;
            http_get(block_url, function (block_items){
                blocks_to_load --;
                block_items.forEach(function (item) {
                    list[item[INDEX]] = item
                });
                on_block_loaded();
            });
        });
        on_block_loaded();
    }


    function ws_connect(){
        set_status(loader_html + 'Connecting');
        ws = new WebSocket(location.protocol.replace('http', 'ws') + '//' + location.host + location.pathname + '/websocket');
        ws.onopen = ws_onopen;
        ws.onclose = ws_onclose;
        ws.onmessage = ws_onmessage;
    }

    function ws_onopen(event) {
        set_status('&#x2713; Connected');
        reconnect_time = 500;
        close_reason = null;

        var state_riders_points = {}
        Object.entries(state.riders_points).forEach(function (entry) {
            var name = entry[0];
            var update = entry[1];
            var points = (update.full_blocks || []).map(function (block) {
                return {'index': block.end_index, 'hash': block.end_hash};
            });
            if (update.partial_block.length) {
                var last_partial_block = update.partial_block[update.partial_block.length - 1]
                points.push({'index': last_partial_block[INDEX], 'hash': last_partial_block[HASH]})
            }
            state_riders_points[name] = points;
        });
        var current_state = {
            'event_data_hash': state.event_data_hash || null,
            'routes_hash': state.routes_hash,
            'riders_points': state_riders_points,
        }
        ws.send(JSON.stringify(current_state))
    }

    function ws_onclose(event) {
        if (!state.live) {
            set_status('');
        } else if (event.reason.startsWith('Server Error:')) {
            set_status(event.reason);
        } else {
            close_reason = '<span style="color: red; font-weight: bold;">X</span> Disconnected: ' + event.reason;
//            console.log(close_reason);
            set_status(close_reason);
            ws = null;

            if (event.reason.startsWith('Error:')){
                reconnect_time = 20000
            } else {
                reconnect_time = Math.min(reconnect_time * 2, 20000)
            }

            function reconnect_status(time){
                set_status(close_reason + '<br>Reconnecting in ' + Math.floor((reconnect_time - time) / 1000) + ' sec.');
            }
            for(var time = 1000; time < reconnect_time; time += 1000){
                setTimeout(reconnect_status, time, time);
            }

            setTimeout(ws_connect, reconnect_time);
          }
    }

    function ws_onmessage(event){
        set_status('&#x2713; Connected');
//        console.log(event.data);
        var need_save_state = false;

        var data = JSON.parse(event.data);
        if (data.hasOwnProperty('server_time')) {
            var current_time = new Date();
            var server_time = new Date(data.server_time * 1000);
            time_offset = (current_time.getTime() - server_time.getTime()) / 1000;
        }
        if (data.hasOwnProperty('client_hash')) {
            if (data.client_hash != client_hash) {
                location.reload();
            }
        }
        if (data.hasOwnProperty('sending')) {
            set_status('&#x2713; Conneceted, ' + loader_html + 'Loading '+ data.sending);
        }
        if (data.hasOwnProperty('live')) {
            state.live = data.live;
            need_save_state = true;
            if (!data.live) {
                get_initial_state();
                ws.close() // The server will also close the ws. Do this just incase.
            }
        }
        if (data.hasOwnProperty('event_data_hash')) {
            state.event_data = data.event_data;
            state.event_data_hash = data.event_data_hash;
            event_data = state.event_data;
            need_save_state = true;
            on_new_event_data();
            update_rider_table();
        }
        if (data.hasOwnProperty('routes_hash')) {
            state.routes_hash = data.routes_hash;
            need_save_state = true;
            on_new_routes_hash()
        }
        if (data.hasOwnProperty('riders_points')) {
            Object.entries(data.riders_points).forEach(function (entry){
                var name = entry[0];
                var update = entry[1];
                var rider_points = riders_points[name] || (riders_points[name] = []);
                var state_riders_points = state.riders_points[name];
                load_update_list('rider_points?name=' + name,
                                 state_riders_points, update, rider_points,
                                 on_new_rider_points.bind(null, name));
            });
            need_save_state = true;
        }

        if (need_save_state) save_state();

    }

    function format_race_time(seconds){
        return sprintf('%02i:%02i:%02i',
            Math.floor(seconds / 60 / 60), /* hours */
            Math.floor(seconds / 60 % 60),      /* min */
            Math.floor(seconds % 60)            /* seconds */
            );
//        return sprintf('%id %02i:%02i:%02i',
//            Math.floor(seconds / 60 / 60 / 24), /* days */
//            Math.floor(seconds / 60 / 60 % 24), /* hours */
//            Math.floor(seconds / 60 % 60),      /* min */
//            Math.floor(seconds % 60)            /* seconds */
//            );
    }

    var race_time = document.getElementById('race_time');
    setInterval(function(){
        if (event_data && event_data.hasOwnProperty('event_start')){
            race_time.innerText = 'Race time: ' + format_race_time((new Date().getTime() / 1000) - event_data.event_start - time_offset);
        } else {
            race_time.innerHTML = '&nbsp;';
        }
    }, 1000);

    function on_new_event_data(){
        event_markers.forEach(function (marker) { marker.setMap(null) });
        event_markers = [];

        if (event_data) {
            document.getElementById('title').innerText = event_data.title;
            document.title = event_data.title;
            riders_by_name = {};
            event_data.riders.forEach(function (rider) { riders_by_name[rider.name] = rider});
            update_rider_table();

            var bounds = new google.maps.LatLngBounds();
            (event_data.markers || {}).forEach(function (marker_data) {
                bounds.extend(marker_data.position);
                var marker = new google.maps.Marker(marker_data);
                marker.setMap(map);
                event_markers.push(marker);
            });
            map.fitBounds(bounds);


        }
    }

    function on_new_routes(){
        route_paths.forEach(function (path) { path.setMap(null) });
        route_paths = routes.map(function (route){
            return new google.maps.Polyline({
                map: map,
                path: route.map(function (point) {return new google.maps.LatLng(point[0], point[1])}),
                geodesic: false,
                strokeColor: 'black',
                strokeOpacity: 0.7,
                strokeWeight: 2,
                zIndex: -1
            })
        });
     }

    function on_new_rider_points(rider_name, index, old_items){

        var rider = riders_by_name[rider_name]
        if (!rider) return;
        var rider_items = riders_client_items[rider_name] || (riders_client_items[rider_name] = {
            'paths': {},
            'marker': null,
            'current_values': {},
            'last_position_point': null,
            'position_point': null,
        });
        var path_color = rider.color || 'black';
        var rider_current_values = rider_items.current_values;

        if (old_items.length) {

            Object.values(rider_items.paths || {}).forEach(function (path){ path.setMap(null) });
            if (rider_items.marker) rider_items.marker.setMap(null);
            index = 0;
            rider_items = riders_client_items[rider_name] = {
                'paths': {},
                'marker': null,
                'current_values': {},
                'last_position_point': null,
                'position_point': null,
            };
        }

        riders_points[rider_name].slice(index).forEach(function (point) {
            if (point.hasOwnProperty(POSITION)) {
                var path = (rider_items.paths[point[TRACK_ID]] || (rider_items.paths[point[TRACK_ID]] = new google.maps.Polyline({
                    map: map,
                    path: [],
                    geodesic: false,
                    strokeColor: path_color,
                    strokeOpacity: 1.0,
                    strokeWeight: 2
                }))).getPath()
                path.push(new google.maps.LatLng(point[POSITION][0], point[POSITION][1]));
                rider_items.last_position_point = rider_items.position_point;
                rider_items.position_point = point;
            }
            Object.assign(rider_current_values, point);
        });

        if (rider_items.position_point) {
            var position = new google.maps.LatLng(rider_items.position_point[POSITION][0], rider_items.position_point[POSITION][1])
            if (!rider_items.marker) {
                var marker_color = rider.color_marker || 'white';
                rider_items.marker = new RichMarker({
                    map: map,
                    position: position,
                    flat: true,
                    content: '<div class="rider-marker" style="background: ' + marker_color + ';">' + (rider.name_short || rider.name)+ '</div>' +
                             '<div class="rider-marker-pointer" style="border-color: transparent ' + marker_color + ' ' + marker_color + ' transparent;"></div>'
                })
            } else {
                rider_items.marker.setPosition(position);
            }
        }
        update_rider_table();
    }

    var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var riders_detail_el = document.getElementById('riders_detail');
    var riders_el = [];
    function update_rider_table(){
        if (event_data) {
            var sorted_riders = event_data.riders.slice();
            sorted_riders.sort(function (a, b){
                var a_rider_items = riders_client_items[a.name] || {};
                var a_current_values = a_rider_items.current_values || {};
                var b_rider_items = riders_client_items[b.name] || {};
                var b_current_values = b_rider_items.current_values || {};

                if (a_current_values.finished_time && !b_current_values.finished_time || a_current_values.finished_time < b_current_values.finished_time) return -1;
                if (!a_current_values.finished_time && b_current_values.finished_time || a_current_values.finished_time > b_current_values.finished_time) return 1;

                if (a_current_values[DIST_ROUTE] && !b_current_values[DIST_ROUTE] || a_current_values[DIST_ROUTE] > b_current_values[DIST_ROUTE]) return -1;
                if (!a_current_values[DIST_ROUTE] && b_current_values[DIST_ROUTE] || a_current_values[DIST_ROUTE] < b_current_values[DIST_ROUTE]) return 1;
                return 0;
            });

            var current_time = (new Date().getTime() / 1000) - time_offset;
            var show_detail = riders_detail_el.checked;
            var rider_rows = sorted_riders.map(function (rider){
                var rider_items = riders_client_items[rider.name] || {};
                var current_values = rider_items.current_values || {};
                var last_position_point = rider_items.last_position_point;
                var position_point = rider_items.position_point;
                var last_position_time;
                var finished_time;
                var speed;
                var rider_status = (rider.hasOwnProperty('status') ? rider.status : current_values.status || '' );
                if (current_values.finished_time) {
                    if (event_data && event_data.hasOwnProperty('event_start')){
                        finished_time = format_race_time(current_values.finished_time - event_data.event_start);
                    } else {
                        var time = new Date(current_values.finished_time * 1000);
                        finished_time = sprintf('%s %02i:%02i:%02i', days[time.getDay()], time.getHours(), time.getMinutes(), time.getSeconds() )
                    }

                }
                if (position_point) {
                    // TODO more than a day
                    var seconds = current_time - position_point[TIME];
                    if (seconds < 60) { last_position_time = '< 1 min ago' }
                    else if (seconds < 60 * 60) { last_position_time = sprintf('%i min ago', Math.floor(seconds / 60))}
                    else { last_position_time = sprintf('%i:%02i ago', Math.floor(seconds / 60 / 60), Math.floor(seconds / 60 % 60))}
                }
                if (position_point && last_position_point && position_point.hasOwnProperty(DIST_RIDDEN) && last_position_point.hasOwnProperty(DIST_RIDDEN) && current_values[STATUS] == 'Active') {
                    speed = Math.round((position_point[DIST_RIDDEN] - last_position_point[DIST_RIDDEN]) / (position_point[TIME] - last_position_point[TIME]) * 3.6 * 10) /10
                }
                if (show_detail) {
                    return '<tr rider_name="' + rider.name + '" class="rider">' +
                           '<td style="background: ' + (rider.color || 'black') + '">&nbsp;&nbsp;&nbsp;</td>' +
                           '<td>' + rider.name + '</td>' +
                           '<td>' + rider_status + '</td>' +
                           '<td>' + (current_values[STATUS] || '') + '</td>' +
                           '<td style="text-align: right">' +  (last_position_time || '') + '</td>' +
//                           '<td style="text-align: right">' + (current_values.hasOwnProperty(DIST_RIDDEN) ? sprintf('%.1f', current_values[DIST_RIDDEN] / 1000) : '') + '</td>' +
                           '<td style="text-align: right">' + (speed || '') + '</td>' +
                           '<td style="text-align: right">' + (current_values.hasOwnProperty(DIST_ROUTE) ? sprintf('%.1f', current_values[DIST_ROUTE] / 1000) : '') + '</td>' +
                           '<td style="text-align: right">' + (finished_time || '') + '</td>' +
                           '</tr>';
                } else {
                    return '<tr rider_name="' + rider.name + '" class="rider">' +
                           '<td style="background: ' + (rider.color || 'black') + '">&nbsp;&nbsp;&nbsp;</td>' +
                           '<td>' + rider.name + '</td>' +
                           '<td style="text-align: right">' + (finished_time || (current_values.hasOwnProperty(DIST_ROUTE) ? sprintf('%.1f km', current_values[DIST_ROUTE] / 1000) : '')) + ' ' + rider_status +'</td>' +
                           '</tr>';
                }
            });
            if (show_detail) {
                document.getElementById('riders_actual').innerHTML =
                    '<table><tr class="head">' +
                    '<td></td>' +
                    '<td>Name</td>' +
                    '<td>Rider<br>Status</td>' +
                    '<td>Tracker<br>Status</td>' +
                    '<td style="text-align: right">Last<br>Position</td>' +
//                    '<td style="text-align: right">Dist<br>Ridden</td>' +
                    '<td style="text-align: right">Current<br>Speed</td>' +
                    '<td style="text-align: right">Dist on<br>Route</td>' +
                    '<td style="text-align: right">Finish<br>Time</td>' +
                    '</tr>' + rider_rows.join('') + '</table>';
            } else {
                document.getElementById('riders_actual').innerHTML =
                    '<table>' + rider_rows.join('') + '</table>';
            }
            riders_el = document.getElementById('riders_actual').querySelectorAll('.rider');
            Array.prototype.forEach.call(riders_el, function (row){
                var rider_name = row.getAttribute('rider_name');
                row.onclick = rider_onclick.bind(null, row, rider_name);
                if (rider_name == selected_rider) row.classList.add('selected');
            });
        }
    }
    setInterval(update_rider_table());
    riders_detail_el.onclick = update_rider_table;

    var selected_rider = null;
    function rider_onclick(row, rider_name, event) {
        Array.prototype.forEach.call(riders_el, function (el){
            el.classList.remove('selected');
        });
        if (selected_rider == rider_name) {
            selected_rider = null;
        } else {
            selected_rider = rider_name;
            row.classList.add('selected');
        }
        var selected_position;
        event_data.riders.forEach(function (rider){
            var rider_items = riders_client_items[rider.name] || {'paths': {}, 'marker': null};

            var zIndex;
            var opacity;
            if (selected_rider && selected_rider==rider.name){
                zIndex = 1000;
                opacity = 1;
                if (rider_items.marker) selected_position = rider_items.marker.getPosition();
            } else if (selected_rider && selected_rider!=rider.name){
                zIndex = 1;
                opacity = 0.3;
            } else {
                zIndex = 1;
                opacity = 1;
            }
            if (rider_items.marker) {
                rider_items.marker.setZIndex(zIndex);
                rider_items.marker.markerContent_.style.opacity = opacity;
            }
            Object.values(rider_items.paths).forEach(function (path) {
                path.setOptions({zIndex: zIndex, strokeOpacity: opacity});
            });
        });
        if (selected_rider) {
            setTimeout(function(){

                apply_mobile_selected('map');
                if (selected_position) {
                    map.panTo(selected_position);
                }
            });
        }
        event_markers.forEach(function (marker) { marker.setOpacity((selected_rider ? 0.5 : 1)) });

        if (event.ctrlKey) {
            var rider_items = riders_client_items[rider_name] || {};
            var position_point = rider_items.position_point;
            if (position_point) {
                window.open('https://www.google.com/maps/place/' + position_point[POSITION][0] + ',' + position_point[POSITION][1], '_blank');
            }
        }
    }


    var event_data = {};
    var event_markers = [];
    var routes = []
    var route_paths = [];
    var riders_by_name = {}
    var riders_client_items = {}
    var riders_points = {}

    var state = JSON.parse(window.localStorage.getItem(location.pathname))
    if (!state){
        get_initial_state();
    } else {
        on_state_loaded();
    }

});

Array.prototype.extend = function (other_array) {
    other_array.forEach(function(v) {this.push(v)}, this);
}
