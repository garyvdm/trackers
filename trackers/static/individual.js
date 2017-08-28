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
        close_reason = '<span style="color: red; font-weight: bold;">X</span> Disconnected: ' + event.reason;
//        console.log(close_reason);
        set_status(close_reason);
        ws = null;

        if (event.reason.startsWith('Server Error')){
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

    function ws_onmessage(event){
        set_status('&#x2713; Connected');
        var data = JSON.parse(event.data);
        if (data.hasOwnProperty('error')) {
            errors.push(data.error);
            update_status();
        }
        if (data.hasOwnProperty('server_time')) {
            var current_time = new Date();
            var server_time = new Date(data.server_time * 1000);
            time_offset = (current_time.getTime() - server_time.getTime()) / 1000;
        }
        if (data.hasOwnProperty('client_hash')) {
            if (data.client_hash != client_hash) {
                location.reload();
            } else {
                // TODO send points hash
                current_state = {
                    'send_points_since': points.length,
                }
                ws.send(JSON.stringify(current_state))
            }
        }
        if (data.hasOwnProperty('sending')) {
            set_status('&#x2713; Conneceted, ' + loader_html + 'Loading '+ data.sending);
        }
        if (data.hasOwnProperty('erase_points')) {
            Object.values(paths || {}).forEach(function (path){ path.setMap(null) });
            if (marker) marker.setMap(null) ;
            points = [];
            paths = {};
            marker = null;
            position_point = null;
        }
        if (data.hasOwnProperty('points')) {
            var last_index = points.length;
            points.extend(data.points)
            on_new_points(last_index)
        }

    }

    function on_new_points(index){
        path_color = 'black';

        points.slice(index).forEach(function (point) {
            if (point.hasOwnProperty(POSITION)) {
                path = (paths[point[TRACK_ID]] || (paths[point[TRACK_ID]] = new google.maps.Polyline({
                    map: map,
                    path: [],
                    geodesic: false,
                    strokeColor: path_color,
                    strokeOpacity: 1.0,
                    strokeWeight: 2
                }))).getPath()
                path.push(new google.maps.LatLng(point[POSITION][0], point[POSITION][1]));
                position_point = point;
            }
        });

        if (position_point) {
            var position = new google.maps.LatLng(position_point[POSITION][0], position_point[POSITION][1])
            if (!marker) {
                marker = new google.maps.Marker({
                    map: map,
                    position: position,
                })
            } else {
                marker.setPosition(position);
            }
            update_last_point();
        }
    }

    var last_active = document.getElementById('last_active');
    function update_last_point() {
        if (position_point) {
            var current_time = (new Date().getTime() / 1000) - time_offset;
            seconds = current_time - position_point[TIME];
            if (seconds < 60) { last_position_time = '< 1 min ago' }
            else if (seconds < 60 * 60) { last_position_time = sprintf('%i min ago', Math.floor(seconds / 60))}
            else { last_position_time = sprintf('%i:%02i ago', Math.floor(seconds / 60 / 60), Math.floor(seconds / 60 % 60))}
        } else {
            last_position_time = '&nbsp;';
        }
        last_active.innerText = last_position_time
    }

    setInterval(update_last_point, 1000);

    var points = [];
    var paths = {};
    var marker = null;
    var position_point = null

    setTimeout(ws_connect, 0);

});

Array.prototype.extend = function (other_array) {
    other_array.forEach(function(v) {this.push(v)}, this);
}
