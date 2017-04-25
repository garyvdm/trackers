var TIME = 't'
var POSITION = 'p'
var TRACK_ID = 'i'
var STATUS = 's'
var DIST_ROUTE = 'o'
var DIST_RIDDEN = 'd'

document.addEventListener('DOMContentLoaded', function() {
    var status = document.getElementById('status');
    var status_msg = '';
    var errors = [];
    var time_offset = 0;

    function update_status(){
        text = errors.slice(-4).concat([status_msg]).join('<br>');
//        console.log(text);
        status.innerHTML = text;
    }

    function set_status(status){
        status_msg = status;
        update_status();
    }

    window.onerror = function (messageOrEvent, source, lineno, colno, error){{
        setTimeout(function () {{
            errors.push(messageOrEvent);
            update_status();
        }}, 100);

        var request = new XMLHttpRequest();
        request.open("POST", '/client_error', true);
        request.send(messageOrEvent + '\n' + (error.stack || source + ':' + lineno + ':' + colno));
        return false;
    }}

    var map = new google.maps.Map(document.getElementById('map'), {
        center: {lat: 0, lng: 0},
        zoom: 2,
        mapTypeId: 'terrain',
        mapTypeControl: true,
        mapTypeControlOptions: {
            position: google.maps.ControlPosition.TOP_RIGHT
        }
    });

    var ws;
    var close_reason;
    var reconnect_time = 1000;

    loader_html = '<span class="l1"></span><span class="l2"></span><span class="l3"></span> '

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
    }

    function ws_onclose(event) {
        if (event.reason.startsWith('TAKEMEOUTError:')) {
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
        var data = JSON.parse(event.data);
        if (data.hasOwnProperty('server_time')) {
            var current_time = new Date();
            var server_time = new Date(data.server_time * 1000);
            time_offset = (current_time.getTime() - server_time.getTime()) / 1000;
        }
        if (data.hasOwnProperty('client_hash')) {
            if (data.client_hash != client_hash) {
                location.reload();
            } else {
                current_state = {
                    'event_data_version': (event_data? event_data.data_version || null : null),
                    'server_version': (event_data? event_data.server_version || null : null),
                }
                rider_indexes = current_state['rider_indexes'] = {}
                Object.keys(riders_points).forEach(function (name) {rider_indexes[name] = riders_points[name].length})
                ws.send(JSON.stringify(current_state))
            }
        }
        if (data.hasOwnProperty('sending')) {
            set_status('&#x2713; Conneceted, ' + loader_html + 'Loading '+ data.sending);
        }
        if (data.hasOwnProperty('event_data')) {
            event_data = data.event_data;
            event_data.server_version = data.server_version;
            window.localStorage.setItem(location.pathname  + '_event_data', JSON.stringify(event_data));
            on_new_event_data();
            update_rider_table();
        }
        if (data.hasOwnProperty('erase_rider_points')) {
            riders_points = {};
            window.localStorage.setItem(location.pathname  + '_riders_points', JSON.stringify(riders_points))
            Object.values(riders_client_items).forEach(function (rider_items){
                Object.values(rider_items.paths || {}).forEach(function (path){ path.setMap(null) });
                if (rider_items.hasOwnProperty('marker')) rider_items.marker.setMap(null);
            });
            riders_client_items = {};
            update_rider_table();
        }
        if (data.hasOwnProperty('rider_points')) {
            var name = data.rider_points.name;
            var rider_points = riders_points[name] || (riders_points[name] = []);
            var last_index = rider_points.length;
            rider_points.extend(data.rider_points.points)
            window.localStorage.setItem(location.pathname  + '_riders_points', JSON.stringify(riders_points))
            on_new_rider_points(name, last_index)
            update_rider_table();
        }

    }

    function format_race_time(seconds){
        return sprintf('%id %02i:%02i:%02i',
            Math.floor(seconds / 60 / 60 / 24), /* days */
            Math.floor(seconds / 60 / 60 % 24), /* hours */
            Math.floor(seconds / 60 % 60),      /* min */
            Math.floor(seconds % 60)            /* seconds */
            );
    }

    var race_time = document.getElementById('race_time');
    setInterval(function(){
        if (event_data && event_data.hasOwnProperty('event_start')){
            race_time.innerText = 'Race time: ' + format_race_time((new Date().getTime() / 1000) - event_data.event_start - time_offset);
        } else {
            race_time.innerText = '.';
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

    function on_new_rider_points(rider_name, index){
        var rider = riders_by_name[rider_name]
        if (!rider) return;
        var rider_items = riders_client_items[rider_name] || (riders_client_items[rider_name] = {'paths': {}, 'current_values': {}})
        path_color = rider.color || 'black';
        var rider_current_values = rider_items.current_values;

        riders_points[rider_name].slice(index).forEach(function (point) {
            if (point.hasOwnProperty(POSITION)) {
                path = (rider_items.paths[point[TRACK_ID]] || (rider_items.paths[point[TRACK_ID]] = new google.maps.Polyline({
                    map: map,
                    path: [],
                    geodesic: true,
                    strokeColor: path_color,
                    strokeOpacity: 1.0,
                    strokeWeight: 2
                }))).getPath()
                path.push(new google.maps.LatLng(point[POSITION][0], point[POSITION][1]));
                rider_items.last_position_point = point;
            }
            Object.assign(rider_current_values, point);
        });

        if (rider_items.hasOwnProperty('last_position_point')) {
            var position = new google.maps.LatLng(rider_items.last_position_point[POSITION][0], rider_items.last_position_point[POSITION][1])
            if (!rider_items.marker) {
                marker_color = rider.color_marker || 'white';
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
    }

    var riders_detail_el = document.getElementById('riders_detail');
    function update_rider_table(){
        if (event_data) {
            var sorted_riders = Array.from(event_data.riders);
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
            rider_rows = sorted_riders.map(function (rider){
                var rider_items = riders_client_items[rider.name] || {};
                var current_values = rider_items.current_values || {};
                var last_position_point = rider_items.last_position_point || {};
                var last_position_time;
                var finished_time;
                if (current_values.finished_time) {
                    if (event_data && event_data.hasOwnProperty('event_start')){
                        finished_time = format_race_time(current_values.finished_time - event_data.event_start);
                    } else {
                        var time = new Date(current_values.finished_time * 1000);
                        finished_time = sprintf('%s %02i:%02i:%02i', days[time.getDay()], time.getHours(), time.getMinutes(), time.getSeconds() )
                    }

                }
                if (rider_items.last_position_point) {
                    // TODO more than a day
                    seconds = current_time - last_position_point[TIME];
                    if (seconds < 60) { last_position_time = '< 1 min ago' }
                    else if (seconds < 60 * 60) { last_position_time = sprintf('%i min ago', Math.floor(seconds / 60))}
                    else { last_position_time = sprintf('%i:%02i ago', Math.floor(seconds / 60 / 60), Math.floor(seconds / 60 % 60))}
                }
                if (show_detail) {
                    return '<tr>'+
                           '<td style="background: ' + (rider.color || 'black') + '">&nbsp;&nbsp;&nbsp;</td>' +
                           '<td>' + rider.name + '</td>' +
                           '<td>' + (current_values[STATUS] || '') + '</td>' +
                           '<td style="text-align: right">' +  (last_position_time || '') + '</td>' +
                           '<td style="text-align: right">' + (current_values.hasOwnProperty(DIST_RIDDEN) ? Math.round(current_values[DIST_RIDDEN] / 100) / 10 : '') + '</td>' +
                           '<td style="text-align: right">' + (last_position_point.hasOwnProperty(DIST_RIDDEN) ? (Math.round(last_position_point[DIST_RIDDEN] / last_position_point[TIME] * 3.6 * 10) /10)   : '') + '</td>' +
                           '<td style="text-align: right">' + (current_values.hasOwnProperty(DIST_ROUTE) ? Math.round(current_values[DIST_ROUTE] / 100) / 10 : '') + '</td>' +
                           '<td style="text-align: right">' + (finished_time || '') + '</td>' +
                           '</tr>';
                } else {
                    return '<tr>'+
                           '<td style="background: ' + (rider.color || 'black') + '">&nbsp;&nbsp;&nbsp;</td>' +
                           '<td>' + rider.name + '</td>' +
                           '<td style="text-align: right">' + (finished_time || (current_values.hasOwnProperty(DIST_RIDDEN) ? (Math.round(current_values[DIST_RIDDEN] / 100) / 10) +' km': '')) + '</td>' +
                           '</tr>';
                }
            });
            if (show_detail) {
                document.getElementById('riders_actual').innerHTML =
                    '<table><tr class="head">' +
                    '<td></td>' +
                    '<td>Name</td>' +
                    '<td>Tracker<br>Status</td>' +
                    '<td style="text-align: right">Last<br>Position</td>' +
                    '<td style="text-align: right">Dist<br>Ridden</td>' +
                    '<td style="text-align: right">Current<br>Speed</td>' +
                    '<td style="text-align: right">Dist on<br>Route</td>' +
                    '<td style="text-align: right">Finish<br>Time</td>' +
                    '</tr>' + rider_rows.join('') + '</table>';
            } else {
                document.getElementById('riders_actual').innerHTML =
                    '<table>' + rider_rows.join('') + '</table>';
            }
        }
    }
    setInterval(update_rider_table());
    riders_detail_el.onclick = update_rider_table;

    var event_data = JSON.parse(window.localStorage.getItem(location.pathname  + '_event_data'))
    var event_markers = []
    var riders_by_name = {}
    var riders_points = JSON.parse(window.localStorage.getItem(location.pathname  + '_riders_points')) || {}
    var riders_client_items = {}

    try{
        if (event_data){
            on_new_event_data();
            Object.keys(riders_points).forEach(function(rider_name) { on_new_rider_points(rider_name, 0) });
            update_rider_table();
        }
    }
    finally {
        setTimeout(ws_connect, 0);
    }

    var main_el = document.getElementById('main');
    var mobile_selectors = document.getElementById('mobile_select').querySelectorAll('div');
    var mobile_selected;

    function apply_mobile_selected(selected){
        mobile_selected = selected;
        main_el.className = 'show_' + selected;
        mobile_selectors.forEach(function (el){
            el.className = (el.getAttribute('show') == selected?'selected':'')
        });
    }
    apply_mobile_selected('map');
    mobile_selectors.forEach(function (el){
        var el_selects = el.getAttribute('show')
        el.onclick = function(){apply_mobile_selected(el_selects);};
    });


});

Array.prototype.extend = function (other_array) {
    other_array.forEach(function(v) {this.push(v)}, this);
}
