document.addEventListener('DOMContentLoaded', function() {
    var status = document.getElementById('status');

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

    function ws_connect(){
        status.innerText = 'Connecting';
        ws = new WebSocket('ws://' + location.host + location.pathname + '/websocket');
        ws.onopen = ws_onopen;
        ws.onclose = ws_onclose;
        ws.onmessage = ws_onmessage;
    }

    function ws_onopen(event) {
        status.innerText = 'Conneceted';
        reconnect_time = 1000;
        close_reason = null;

        current_state = {
            'event_data_version': (event_data? event_data['data_version'] || null : null),
        }
        rider_indexes = current_state['rider_indexes'] = {}
        Object.keys(riders_points).forEach(function (name) {rider_indexes[name] = riders_points[name].length})
        console.log(current_state)

        ws.send(JSON.stringify(current_state))
    }

    function ws_onclose(event) {
        if (event.reason.startsWith('TAKEMEOUTError:')) {
            status.innerText = event.reason;
        } else {
            close_reason = 'Disconnected: ' + event.code + ' ' + event.reason;
            status.innerText = close_reason;
            ws = null;

            if (event.reason.startsWith('Error:')){
                reconnect_time = 20000
            } else {
                reconnect_time = Math.min(reconnect_time * 2, 20000)
            }

            function reconnect_status(time){
                status.innerText = close_reason + '\nReconnecting in ' + Math.floor((reconnect_time - time) / 1000) + ' sec.';
            }
            for(var time = 1000; time < reconnect_time; time += 1000){
                setTimeout(reconnect_status, time, time);
            }

            setTimeout(ws_connect, reconnect_time);
          }
    }

    function ws_onmessage(event){
        status.innerText = 'Conneceted';
//        console.log(event.data);
        var data = JSON.parse(event.data);
        if (data.hasOwnProperty('client_etags')) {
            data.client_etags.forEach(function (item){
                var request = new XMLHttpRequest();
                request.onreadystatechange = function() {
                    if (request.readyState == 4) {
                        if (item[1] != request.getResponseHeader('etag')) {
                            location.reload(true);
                        }
                    }
                }
                request.open("GET", (item[0]?item[0]:location.pathname), true);
                request.send(null);
            })
        }
        if (data.hasOwnProperty('sending')) {
            status.innerText = 'Conneceted, Loading '+ data.sending;
        }
        if (data.hasOwnProperty('event_data')) {
            event_data = data.event_data;
            window.localStorage.setItem(location.pathname  + '_event_data', JSON.stringify(event_data));
            on_new_event_data();
        }
        if (data.hasOwnProperty('erase_rider_points')) {
            riders_points = {};
            window.localStorage.setItem(location.pathname  + '_riders_points', JSON.stringify(riders_points))
        }
        if (data.hasOwnProperty('rider_points')) {
            var name = data.rider_points.name;
            var rider_points = riders_points[name] || (riders_points[name] = []);
            rider_points.extend(data.rider_points.points)
            window.localStorage.setItem(location.pathname  + '_riders_points', JSON.stringify(riders_points))
        }

    }

    function on_new_event_data(){
        if (event_data) {
            document.title = event_data.title;
            riders_by_name = {}
            event_data.riders.forEach(function (rider) { riders_by_name[rider.name] = rider})
        }
    }

    function on_new_rider_points(rider_name, index){
        rider = riders_by_name[rider_name]
        if (!rider) return;
        rider_items = riders_client_items[rider_name] || (riders_client_items[rider_name] = {})
        path = (rider_items.path || (rider_items.path = new google.maps.Polyline({
            map: map,
            path: [],
            geodesic: true,
            strokeColor: rider.color,
            strokeOpacity: 1.0,
            strokeWeight: 2
        }))).getPath()

        var last_position = null;
        riders_points[rider_name].slice(index).forEach(function (point) {
            if (point.hasOwnProperty('position')) {
                last_position = new google.maps.LatLng(point.position[0], point.position[1]);
                path.push(last_position);
            }
        });

        marker = (rider_items.marker || (rider_items.path = new google.maps.Marker({
            map: map,
            title: rider.name,
            label: rider.name.substr(0, 1),
            color: rider.color
        }))).setPosition(last_position);


    }

    var event_data = JSON.parse(window.localStorage.getItem(location.pathname  + '_event_data'))
    var riders_by_name = {}
    var riders_points = JSON.parse(window.localStorage.getItem(location.pathname  + '_riders_points')) || {}
    var riders_client_items = {}

    try{
        on_new_event_data();
        Object.keys(riders_points).forEach(function(rider_name) { on_new_rider_points(rider_name, 0) });
    }
    finally {
        setTimeout(ws_connect, 0);
    }

});

Array.prototype.extend = function (other_array) {
    other_array.forEach(function(v) {this.push(v)}, this);
}
