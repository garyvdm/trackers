"use strict";

got_to_loading = true;
var loader_html = '<span class="l1"></span><span class="l2"></span><span class="l3"></span> '


function get(url) {
    return fetch(location.pathname + url)
        .catch(promise_catch)
        .then( function(response) {
            if (response.ok) {
                return response.json();
            } else {
                response.text().then(function (error) {
                    console.log(error);
                    errors.push(error);
                    update_status();
                });
            }
        })
        .catch(promise_catch);
}

function load_state(){
    set_status(loader_html + 'Loading');
    var new_state = JSON.parse(window.localStorage.getItem(location.pathname))
    if (!new_state){
        get_state();
    } else {
        on_new_state_received_non_ws(new_state);
    }
}

function save_state(state){
    var storage = window.localStorage;
    if (state.live) {
        try{
            storage.setItem(location.pathname, JSON.stringify(state));
        } catch(error) {
            var keys = [];
            var key;
            for (var i=0; i<storage.length; i++) {
                key = storage.key(i);
                keys.push(key);
            }
            keys.forEach(function (key) { storage.removeItem(key); });
            log_to_server('Error in save_state. Storage was cleared. Will retry save.\nError: ' + error.toString() + '\nStorage keys: '+keys.toString());
            storage.setItem(location.pathname, JSON.stringify(state));
        }
    } else {
        storage.removeItem(location.pathname);
    }
}

function get_state(){
    get('/state')
        .then(on_new_state_received_non_ws)
        .catch(promise_catch);
}

function on_new_state_received_non_ws(new_state){
    try{
        on_new_state_received(new_state);
    }
    finally {
        if (new_state.live) {
            ws_ensure_connect();
        } else {
            ws_close();
        }
    }
    if (new_state.loading) {
        set_status(loader_html + 'Server processing data');
        setTimeout(get_state, 1000)
    }
}

var state = {}
var is_live_loaded = new Deferred();
var config;
var config_loaded = new Deferred();
var routes = []
var all_route_points = [];
var time_show_days = true;

var event_markers = [];
var route_paths = [];
var riders_by_name = {};
var riders_client_items = {};
var riders_points = {};
var riders_off_route = {};
var riders_pre_post = {};
var riders_values = {};
var riders_pre_post_values = {};
var riders_predicted = {}

function on_new_state_received(new_state) {
    var need_save = false;

    if (new_state.hasOwnProperty('server_time')) {
        var current_time = new Date();
        var server_time = new Date(new_state.server_time * 1000);
        time_offset = (current_time.getTime() - server_time.getTime()) / 1000;
    }
    if (new_state.hasOwnProperty('client_hash')) {
        if (new_state.client_hash != client_hash) {
            location.reload();
            return;
        }
    }

    if (new_state.hasOwnProperty('live')) {
        state.live = new_state.live;
        need_save = true;
        is_live_loaded.resolve();
    }
    if (new_state.hasOwnProperty('config_hash') && state.config_hash != new_state.config_hash) {
        event_markers.forEach(function (marker) { marker.setMap(null) });
        event_markers = [];
        // console.log('clear markers')
        Object.keys(riders_client_items).forEach(function (rider_name){
            var rider_items = riders_client_items[rider_name]
            Object.values(rider_items.paths || {}).forEach(function (paths){
                Object.values(paths || {}).forEach(function (path){ path.setMap(null) });
            });
            if (rider_items.marker) rider_items.marker.setMap(null);
            var series = elevation_chart.get(rider_name);
            if (series) series.remove();
            Object.values(rider_items.point_markers).forEach(function (marker) {marker.setMap(null)});
        });

        riders_by_name = {};
        riders_client_items = {};
        riders_points = {};
        riders_values = {};
        riders_predicted = {};
        riders_points = {};
        riders_off_route = {};
        riders_pre_post = {};
        if (config_loaded.promise.PromiseStatus == 'pending') config_loaded.reject();
        config_loaded = new Deferred();

        state.config_hash = new_state.config_hash;
        get('/config?hash=' + new_state.config_hash).then(function (new_config){
            config = new_config;
            on_new_config();
            config_loaded.resolve()
        }).catch(promise_catch);
        need_save = true;
    }
    if (new_state.hasOwnProperty('routes_hash') && state.routes_hash != new_state.routes_hash) {
        // console.log('clear routes');
        route_paths.forEach(function (path) { path.setMap(null) });
        elevation_chart.series.forEach(function (series) { series.remove(false) });

        state.routes_hash = new_state.routes_hash;
        get('/routes?hash=' + state.routes_hash).then(function (new_routes){
            routes = new_routes;
            // console.log('routes loaded');
            on_new_routes();
        }).catch(promise_catch);
        need_save = true;
    }
    config_loaded.promise.then( function () {
        var need_update_rider_table = false;
        var riders_updated = {};

        if (new_state.hasOwnProperty('riders_values')) {
            Object.entries(new_state.riders_values).forEach(function (entry){
                var name = entry[0];
                var values = entry[1];
                riders_values[name] = values;
            });
            Object.assign(riders_updated, new_state.riders_values);
            need_update_rider_table = true;
        }
        if (new_state.hasOwnProperty('riders_pre_post_values')) {
            Object.entries(new_state.riders_pre_post_values).forEach(function (entry){
                var name = entry[0];
                var values = entry[1];
                riders_pre_post_values[name] = values;
            });
            if (pre_post_el.checked) Object.assign(riders_updated, new_state.riders_pre_post_values);
            need_update_rider_table = true;
        }
        if (new_state.hasOwnProperty('riders_predicted')) {
            riders_predicted = new_state.riders_predicted;
            Object.assign(riders_updated, riders_predicted);
            need_update_rider_table = true;
        }
        if (need_update_rider_table) update_rider_table();
        Object.keys(riders_updated).forEach(on_new_rider_values);

        [
            ['riders_points', riders_points],
            ['riders_off_route', riders_off_route],
            ['riders_pre_post', riders_pre_post],
        ].forEach(function(item){
            var list_name = item[0];
            var list_container = item[1];
            if (new_state.hasOwnProperty(list_name)) {
                Object.entries(new_state[list_name]).forEach(function (entry){
                    var name = entry[0];
                    var update = entry[1];

                    var list = list_container[name] || [];

                    function fetch_block(block) {
                        return get('/' + list_name + '?name=' + encodeURIComponent(name) + '&start_index=' + block.start_index +
                                   '&end_index=' + block.end_index + '&end_hash=' + block.end_hash);
                    }

                    process_update_list(fetch_block, list, update).then(function (rider_points) {
                        list_container[name] = rider_points.new_list;
                        on_new_rider_points(name, list_name, rider_points.new_list, rider_points.new_items, rider_points.old_items);
                    });
                });
            }
        });

    }).catch(promise_catch);

    if (need_save) save_state(state);
}


var ws;
var ws_connected = false;
var close_reason;
var reconnect_time = 1000;

var time_offset = 0;

var ws_connection_wanted = false;


function ws_ensure_connect(){
    ws_connection_wanted = true;
    if (ws_connection_wanted && !ws) {
        set_status(loader_html + 'Connecting');
        if (!window.WebSocket) {
            document.getElementById('badbrowser').display = 'block';
            log_to_server('No WebSocket support');
        }
        ws = new WebSocket(location.protocol.replace('http', 'ws') + '//' + location.host + location.pathname + '/websocket');
        ws.onopen = ws_onopen;
        ws.onclose = ws_onclose;
        ws.onmessage = ws_onmessage;
    }

}

function ws_close(){
    ws_connection_wanted = false;
    if (ws){
        ws.close();
    } else {
        set_status('');
    }
}

function ws_onopen(event) {
    set_status('&#x2713; Connected');
    reconnect_time = 500;
    close_reason = null;
    ws_connected = true;
    send_subscriptions_to_ws();
}

function reconnect_status(time){
    set_status(close_reason + '<br>Reconnecting in ' + Math.floor((reconnect_time - time) / 1000) + ' sec.');
}

function ws_onclose(event) {
    ws = null;
    ws_connected = false;
    if (!ws_connection_wanted) {
        set_status('');
    } else if (event.reason.startsWith('Server Error:')) {
        set_status(event.reason);
    } else {
        close_reason = '<span style="color: red; font-weight: bold;">X</span> Disconnected: ' + event.reason;
        set_status(close_reason);

        if (event.reason.startsWith('Error:')){
            reconnect_time = 20000
        } else {
            reconnect_time = Math.min(reconnect_time * 2, 20000)
        }


        for(var time = 1000; time < reconnect_time; time += 1000){
            setTimeout(reconnect_status, time, time);
        }

        setTimeout(ws_ensure_connect, reconnect_time);
    }
}

function ws_onmessage(event){
    set_status('&#x2713; Connected');
//    console.log(event.data);

    var data = JSON.parse(event.data);
    if (data.hasOwnProperty('sending')) {
        set_status('&#x2713; Conneceted, ' + loader_html + 'Loading '+ data.sending);
    }
    on_new_state_received(data);
    if (data.hasOwnProperty('live')) {
        if (!data.live){
            ws_close();
            get_state();
        }
    }
}

var subscriptions = {};
var non_live_subscriptions_got = {};

function subscriptions_updated() {
    is_live_loaded.promise.then(function (){
        if (state.live) {
            if (ws_connected) send_subscriptions_to_ws();
        } else {
            Object.keys(subscriptions).forEach(function (name) {
                if (subscriptions[name] > 0 && !non_live_subscriptions_got[name]) {
                    non_live_subscriptions_got[name] = true;
                    if (name=='riders_points') {
                        get('/riders_points').then(function(data) {
                            on_new_state_received({'riders_points': data});
                        }).catch(promise_catch);
                    }
                    if (name.slice(0, 14)=='riders_points.') {
                        var rider_name = name.slice(14);
                        get('/riders_points').then(function(data) {
                            var data_filtered = {};
                            data_filtered[rider_name] = data[rider_name];
                            on_new_state_received({'riders_points': data_filtered});
                        }).catch(promise_catch);
                    }
                    if (name=='riders_off_route') {
                        get('/riders_off_route').then(function(data) {
                            on_new_state_received({'riders_off_route': data});
                        }).catch(promise_catch);
                    }
                }
            });
        }
    });
}

function send_subscriptions_to_ws(){
    var subscriptions_for_server = Object.keys(subscriptions).filter(function (name) {return subscriptions[name] > 0});
    var data = JSON.stringify({'subscriptions': subscriptions_for_server});
    // console.log(data);
    ws.send(data);
}

var graph_select = document.getElementById('graph_select');
graph_select.onchange = update_graph;

var main_el = document.getElementById('foo');
var mobile_selectors = document.getElementById('mobile_select').querySelectorAll('div');
var mobile_selected;
var last_mobile_selected = 'map';

function apply_mobile_selected(selected){
    mobile_selected = selected;
    ['show_map', 'show_graphs', 'show_riders', 'show_options'].forEach(function(className){ if (main_el.classList.contains(className)) main_el.classList.remove(className); });
    main_el.classList.add('show_' + selected);
    Array.prototype.forEach.call(mobile_selectors, function (el){
        el.className = (el.getAttribute('show') == selected?'selected':'')
    });
    if (selected=='map') {
        google.maps.event.trigger(map, 'resize');
        last_mobile_selected = selected;
    }
    if (selected=='graphs') {
        update_graph();
        last_mobile_selected = selected;
    }
    if (mobile_selected != 'graphs' && desktop_main_selected != 'graphs') remove_graph_subscriptions();
}

Array.prototype.forEach.call(mobile_selectors, function (el){
    var el_selects = el.getAttribute('show')
    el.onclick = function(){apply_mobile_selected(el_selects);};
});


var desktop_selectors = document.getElementById('desktop_select').querySelectorAll('div');
var desktop_selected;

function apply_desktop_selected(selected){
    desktop_selected = selected;
    ['desktop_show_riders', 'desktop_show_options'].forEach(function(className){ if (main_el.classList.contains(className)) main_el.classList.remove(className); });
    main_el.classList.add('desktop_show_' + selected);
    Array.prototype.forEach.call(desktop_selectors, function (el){
        el.className = (el.getAttribute('show') == selected?'selected':'')
    });
}

Array.prototype.forEach.call(desktop_selectors, function (el){
    var el_selects = el.getAttribute('show')
    el.onclick = function(){apply_desktop_selected(el_selects);};
});


var desktop_main_selectors = document.getElementById('desktop_main_select').querySelectorAll('div');
var desktop_main_selected;

function apply_desktop_main_selected(selected){
    desktop_main_selected = selected;
    ['desktop_show_map', 'desktop_show_graphs'].forEach(function(className){ if (main_el.classList.contains(className)) main_el.classList.remove(className); });
    main_el.classList.add('desktop_show_' + selected);
    Array.prototype.forEach.call(desktop_main_selectors, function (el){
        el.className = (el.getAttribute('show') == selected?'selected':'')
    });
    if (selected=='map') google.maps.event.trigger(map, 'resize');
    if (selected=='graphs') update_graph();
    if (mobile_selected != 'graphs' && desktop_main_selected != 'graphs') remove_graph_subscriptions();
}

Array.prototype.forEach.call(desktop_main_selectors, function (el){
    var el_selects = el.getAttribute('show')
    el.onclick = function(){apply_desktop_main_selected(el_selects);};
});


var map;
var route_marker;
var point_info_window = new google.maps.InfoWindow();

var elevation_chart = Highcharts.chart('elevation', {
    chart: { type: 'line', height: null },
    title: { text: 'Elevation', style: {display: 'none'} },
    legend:{ enabled: false },
    xAxis: { id: 'xAris', type: 'linear',
        labels: { formatter: function () { return (Math.round(this.value / 100) / 10).toString() + " km"; }}
    },
    yAxis: { title: {text: null}, endOnTick: false, startOnTick: false, labels: {format: '{value} m'} },
    credits: { enabled: false },
    series: [],
    tooltip: {
        formatter: function() {
            if (this.series.name.startsWith('route-')) {
                return (Math.round(this.x / 100) / 10).toString() + " km : " +  Math.round(this.y).toString() + ' m';
            } else {
                return (this.key);
            }
        }
    },
});


var race_time = document.getElementById('race_time');
setInterval(function(){
    if (config && config.hasOwnProperty('event_start')){
        var event_type = config.display_type || 'Race';
        var race_time_seconds = (new Date().getTime() / 1000) - config.event_start - time_offset
        if (race_time_seconds >= 0 && state.live) {
            race_time.innerText = event_type + ' time: ' + format_time_delta((new Date().getTime() / 1000) - config.event_start - time_offset, time_show_days);
        } else {
            race_time.innerText = event_type + ' start time: ' + new Date(config.event_start * 1000).toLocaleString(date_locale, date_options_full);
        }
    } else {
        race_time.innerHTML = '&nbsp;';
    }
}, 1000);

function on_new_config(){
    if (config) {
        time_show_days = config['time_show_days'] || false;

        if (!map) {
            map = new google.maps.Map(document.getElementById('map_el'), {
                bounds: bounds,
                mapTypeId: 'terrain',
                mapTypeControl: true,
                mapTypeControlOptions: {
                    position: google.maps.ControlPosition.TOP_RIGHT
                }
            });

            apply_mobile_selected('map');
            map.addListener('bounds_changed', function() {
                if (bounds_changed_timeout_id) clearTimeout(bounds_changed_timeout_id);
                bounds_changed_timeout_id = setTimeout(function (){
                    bounds_changed_timeout_id = null;
                    adjust_elevation_chart_bounds();
                }, 200);
                update_selected_rider_point_markers();
            });
            map.addListener('click', function(e) {
                console.log(e.latLng.toUrlValue())

            });
            route_marker = new google.maps.Marker({
              icon: {
                path: google.maps.SymbolPath.CIRCLE,
                scale: 2,
                strokeColor: 'black',
              },
              draggable: false,
              map: map
            });;
            route_marker.setVisible(false)
        }

        (config.markers || {}).forEach(function (marker_data) {
            var marker;
            if (!marker_data.hasOwnProperty('marker_text')) {
                marker = new google.maps.Marker(marker_data);
            } else {
                marker = new RichMarker({
                    position: new google.maps.LatLng(marker_data.position.lat, marker_data.position.lng),
                    content: '<div class="rider-marker" style="background: black; color: white;" title="' + marker_data.title + '">' + marker_data.marker_text + '</div>' +
                             '<div class="rider-marker-pointer" style="border-color: transparent black black transparent;"></div>',
                    flat: true,
                });
            }
            marker.setMap(map);
            event_markers.push(marker);
        });

        if (config.hasOwnProperty('bounds')) {
            map.fitBounds(config.bounds);
        } else {
            var bounds = new google.maps.LatLngBounds();
            (config.markers || {}).forEach(function (marker_data) {
                bounds.extend(marker_data.position);
            });
            map.fitBounds(bounds);
        }


        // console.log('set riders_client_items');
        config.riders.forEach(function (rider) {
            riders_by_name[rider.name] = rider
            riders_client_items[rider.name] = {
                paths: {
                    riders_points: [],
                    riders_off_route: [],
                    riders_pre_post: []
                },
                point_markers: {},
                marker: null,
            };
            if (riders_values.hasOwnProperty(rider.name)) {
                on_new_rider_values(rider.name);
            }
        });
        elevation_chart.redraw(false);
        update_rider_table();

    }
}

var route_marker;


function on_new_routes(){
    config_loaded.promise.then( function () {
        // console.log('add routes');
        var i = 0;
        route_paths = routes.map(function (route){
            i ++;
            return new google.maps.Polyline({
                map: map,
                path: route.points.map(function (point) {return new google.maps.LatLng(point[0], point[1])}),
                geodesic: false,
                strokeColor: (i==1?'black':'#444444'),
                strokeOpacity: 0.7,
                strokeWeight: 2,
                zIndex: -1
            })
        });

        var i = 0;
        all_route_points = [];
        routes.forEach(function (route){
            var start_distance, dist_factor
            if (route.main) {
                start_distance = 0;
                dist_factor = 1;
            } else {
                start_distance = route.start_distance;
                dist_factor = route.dist_factor;
            }
            var elevation_points = route.elevation.map(function (point) {
                return {
                    latlng: new google.maps.LatLng(point[0], point[1]),
                    dist: (point[3] * dist_factor) + start_distance,
                    elevation: point[2],
                }
            });
            all_route_points.extend(elevation_points);
            i ++;

            elevation_chart.addSeries({
                marker: {enabled: false, symbol: 'circle', radius: 2},
                color: (i==1?'black':'#444444'),
                turboThreshold: 5000,
                name: 'route-' + i,
                data: elevation_points.map(function (item) { return {
                    x: item.dist,
                    y: item.elevation,
                    latlng: item.latlng,
                    events: {
                      mouseOver: function () {
                        route_marker.setOptions({position: this.latlng})
                        route_marker.setVisible(true);
                        // if (!map.getBounds().contains(this.latlng)) map.panTo(this.latlng);
                      }
                    }

                }}),
                events: {
                    mouseOut: function () {
                      route_marker.setVisible(false);
                    },
                },
                zIndex: -1,

            }, false, false);
        });
        elevation_chart.redraw(false);
        adjust_elevation_chart_bounds();
    }).catch(promise_catch);
}

var bounds_changed_timeout_id;

function adjust_elevation_chart_bounds() {
    var bounds = map.getBounds();
    if (bounds) {
        // TODO optimise this search.
        var min = Infinity;
        var max = -Infinity;
        all_route_points.forEach( function (point) {
            if (bounds.contains(point.latlng)) {
                if (point.dist < min) min = point.dist;
                if (point.dist > max) max = point.dist;
            }
        });
        var adjust = (max - min) * 0.01;
        elevation_chart.xAxis[0].setExtremes(min - adjust, max + adjust, true, false);
    } else {
        elevation_chart.xAxis[0].setExtremes(null, null, true, false);
    }
}

function get_radio_value (name) {
    var value;
    var radios = document.getElementsByName(name);
    Array.prototype.forEach.call(radios, function (radio) {
        if (radio.checked) value = radio.value;
    });
    return value;
}


var show_routes;

function onclick_show_routes() {
    if (show_routes) subscriptions[show_routes] = Math.max((subscriptions[show_routes] || 0) - 1, 0);
    show_routes = get_radio_value('show_routes');

    Object.keys(riders_client_items).forEach(update_rider_paths_visible);

    subscriptions[show_routes] = Math.max((subscriptions[show_routes] || 0) + 1, 0);
    subscriptions_updated();
}

function show_route_for_rider(route_name, rider_name) {
    if (route_name == 'riders_pre_post'){
        return pre_post_el.checked;
    }
    return (selected_riders.has(rider_name)?route_name == 'riders_points':route_name == show_routes);
}

function update_rider_paths_visible(rider_name) {
    var rider_client_items = riders_client_items[rider_name];
    Object.keys(rider_client_items.paths).forEach( function (route_name) {
        var paths = rider_client_items.paths[route_name];
        var show = show_route_for_rider(route_name, rider_name);
        paths.forEach(function (path) { path.setVisible(show) });
    });
}

onclick_show_routes();
Array.prototype.forEach.call(document.getElementsByName('show_routes'), function(radio) { radio.onclick = onclick_show_routes; });

function on_new_rider_points(rider_name, list_name, items, new_items, old_items){
    config_loaded.promise.then( function () {
        var rider = riders_by_name[rider_name]
        if (!rider) return;
        var rider_items = riders_client_items[rider_name];
        var paths = rider_items.paths[list_name];
        if (old_items.length) {
            Object.values(paths).forEach(function (path){ path.setMap(null) });
            rider_items.paths[list_name] = paths = [];
            if (list_name == 'riders_points') {
                Object.values(rider_items.point_markers).forEach(function (marker) {marker.setMap(null)});
                rider_items.point_markers = {};
            }
            new_items = items;
        }

        var path_color = rider.color || 'black';
        if (list_name == 'riders_pre_post' && rider.color_pre_post) {path_color = rider.color_pre_post}

        var rider_current_values = rider_items.current_values;
        var visible = show_route_for_rider(list_name, rider_name);
        new_items.forEach(function (point) {
            if (point.hasOwnProperty('position')) {
                var track_id = (point.hasOwnProperty('track_id')? point.track_id : 0);
                var path = paths[track_id];
                if (!path) {
                    path = paths[track_id] = new google.maps.Polyline({
                        map: map,
                        path: [],
                        geodesic: false,
                        strokeColor: path_color,
                        strokeOpacity: 1.0,
                        strokeWeight: 2,
                        visible: visible,
                        zIndex: (list_name == 'riders_points'? 1: 0),
                    });
                    path.addListener('click', select_rider.bind(null, rider_name, true, false));
                }
                path.getPath().push(new google.maps.LatLng(point.position[0], point.position[1]));
            }
        });

        if (selected_riders.has(rider_name) && list_name == 'riders_points') update_selected_rider_point_markers();
        on_new_rider_points_graph(rider_name, list_name, items, new_items, old_items, true);
    }).catch(promise_catch);
}

var pre_post_el = document.getElementById('pre_post')
pre_post_el.onclick = function () {
    var old_riders_pre_post_values = riders_pre_post_values;
    if (!pre_post_el.checked) riders_pre_post_values = {};
    Object.keys(old_riders_pre_post_values).forEach(on_new_rider_values);
    update_rider_table();
    Object.keys(riders_client_items).forEach(update_rider_paths_visible);

    subscriptions['riders_pre_post'] = (pre_post_el.checked?1:0)
    subscriptions_updated();
}
pre_post_el.onclick();


var predicted_el = document.getElementById('predicted');
predicted_el.onclick = function () {
    var old_riders_predicted = riders_predicted;
    if (!predicted_el.checked) riders_predicted = {};
    Object.keys(old_riders_predicted).forEach(on_new_rider_values);
    update_rider_table();
    subscriptions['riders_predicted'] = (predicted_el.checked?1:0)
    subscriptions_updated();
};
predicted_el.onclick();


function on_new_rider_values(rider_name){

        var rider = riders_by_name[rider_name]
        if (!rider) return;
        var rider_items = riders_client_items[rider_name];
        var values = {};
        Object.assign(values, riders_values[rider_name] || {});
        if (pre_post_el.checked && riders_pre_post_values.hasOwnProperty(rider_name)) {
            Object.assign(values, riders_pre_post_values[rider_name]);
        }
        if (predicted_el.checked && riders_predicted.hasOwnProperty(rider_name)) {
            Object.assign(values, riders_predicted[rider_name]);
        }
        var show = pre_post_el.checked || !values.hasOwnProperty('rider_status') || selected_riders.has(rider_name);

        var marker_color = rider.color_marker || 'white';

        if (show && values.hasOwnProperty('position')) {
            var position = new google.maps.LatLng(values.position[0], values.position[1])
            if (!rider_items.marker) {
                rider_items.marker = new google.maps.Marker({
                    icon: {
                        path: google.maps.SymbolPath.CIRCLE,
                        scale: 4,
                        strokeColor: marker_color,
                        strokeOpacity: 1,
                        fillColor: marker_color,
                        fillOpacity: 0.8,
                    },
                    draggable: false,
                    map: map,
                    position: position,
                    title: rider.name
                });
                rider_items.marker.addListener('click', select_rider.bind(null, rider_name, true, false));


            } else {
                rider_items.marker.setPosition(position);
            }
            if (selected_riders.has(rider_name)) {
                if (!rider_items.rich_marker) {
                    var marker_html = '<div class="rider-marker" style="background: ' + marker_color + ';">' + (rider.name_short || rider.name)+ '</div>' +
                                      '<div class="rider-marker-pointer" style="border-color: transparent ' + marker_color + ' ' + marker_color + ' transparent;"></div><div style="height:2px;"></div>';

                    rider_items.rich_marker = new RichMarker({
                        map: map,
                        position: position,
                        flat: true,
                        content: marker_html
                    })
//                    rider_items.rich_marker.addListener('click', select_rider.bind(null, rider_name, true));
                } else {
                    rider_items.rich_marker.setPosition(position);
                }
            } else {
                if (rider_items.rich_marker) {
                    rider_items.rich_marker.setMap(null);
                    rider_items.rich_marker = null;
                }
            }
        } else {
            if (rider_items.marker) {
                rider_items.marker.setMap(null);
                rider_items.marker = null;
            }
            if (rider_items.rich_marker) {
                rider_items.rich_marker.setMap(null);
                rider_items.rich_marker = null;
            }

        }
        var series = elevation_chart.get(rider_name);
        if (show && values.hasOwnProperty('dist_route')) {
            var elevation = 0;
            if (values.hasOwnProperty('position') && values.position.length > 2) {
                elevation = values.position[2]
            } else if (values.hasOwnProperty('route_elevation')) {
                elevation = values.route_elevation;
            }

            if (!series) {
                elevation_chart.addSeries({
                    id: rider_name,
                    name: rider_name,
                    marker: { symbol: 'circle', radius: 6},
                    color: marker_color,
                    data: [],
                    events: {
                        click: select_rider.bind(null, rider_name, true, false),
                    },
                }, false);
                series = elevation_chart.get(rider_name);
            }
            series.setData([{
                x: values['dist_route'],
                y: elevation,
                name: rider_name,
//                dataLabels: {
//                    enabled: true,
//                    format: rider.name_short || rider.name,
//                    allowOverlap: true,
//                    shape: 'callout',
//                    backgroundColor: rider.color_marker || 'white',
//                    style: {
//                        textOutline: 'none'
//                    }
//                },
            }], true, false);
        } else {
            if (series) series.remove();
        }

}

var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
var riders_detail_level_el = document.getElementById('riders_detail_level');
var riders_el = [];

function get_rider_values_and_sorted_riders(){
    var riders_values_l = riders_values;
    if (predicted_el.checked) {
        riders_values_l = Object.assign({}, riders_values_l);
        Object.keys(riders_values_l).forEach(function (rider_name) {
            if (riders_predicted.hasOwnProperty(rider_name)) {
                var values = riders_values_l[rider_name];
                values = Object.assign({}, values);
                Object.assign(values, riders_predicted[rider_name]);
                riders_values_l[rider_name] = values;
            }
        });

    }

    var sorted_riders = config.riders.slice();
    sorted_riders.sort(function (a, b){
        var a_values = riders_values_l[a.name] || {};
        var b_values = riders_values_l[b.name] || {};

        if (a_values.finished_time && !b_values.finished_time || a_values.finished_time < b_values.finished_time) return -1;
        if (!a_values.finished_time && b_values.finished_time || a_values.finished_time > b_values.finished_time) return 1;

        if (a_values.dist_route && !b_values.dist_route || a_values.dist_route > b_values.dist_route) return -1;
        if (!a_values.dist_route && b_values.dist_route || a_values.dist_route < b_values.dist_route) return 1;
        return 0;
    });
    return [riders_values_l, sorted_riders];
}

var update_rider_table_timeout_id = null;

function update_rider_table(){
    if (config) {
        if (update_rider_table_timeout_id) clearTimeout(update_rider_table_timeout_id);
        update_rider_table_timeout_id = setTimeout(update_rider_table, 20000)

        var x = get_rider_values_and_sorted_riders();
        var riders_values_l = x[0];
        var sorted_riders = x[1];

        document.getElementById('riders_options').className = (config.riders.length >= 10? 'big':'small')

        var current_time = (new Date().getTime() / 1000) - time_offset;
        update_rider_table_specific(
            document.getElementById('riders_actual'),
            riders_detail_level_el.value,
            sorted_riders, riders_values_l, current_time);
        update_rider_table_specific(
            document.getElementById('graphs_riders'),
            'simple', sorted_riders, riders_values_l, current_time);
    }
}

function update_rider_table_specific(container, detail_level, sorted_riders, riders_values_l, current_time) {
    var expect_off_route = config.expect_off_route || false;
    var rider_rows = sorted_riders.map(function (rider){
        var rider_items = riders_client_items[rider.name];
        var values = riders_values_l[rider.name] || {};
        var last_position_time;
        var last_server_time;
        var finished_time;
        var speed;
        var leader_time_diff = '';
        var rider_status = (rider.hasOwnProperty('status') ? rider.status : values.rider_status || '' );
        if (values.finished_time) {
            if (config && config.hasOwnProperty('event_start')){
                finished_time = format_time_delta(values.finished_time - config.event_start, time_show_days);
            } else {
                var time = new Date(values.finished_time * 1000);
                finished_time = sprintf('%s %02i:%02i:%02i', days[time.getDay()], time.getHours(), time.getMinutes(), time.getSeconds() )
            }
        }
        var last_position_long_ago = true;
        if (values.hasOwnProperty('position_time')) {
            last_position_long_ago = (current_time - values.position_time > 5 * 60)
            last_position_time = format_time_delta_ago_with_date(current_time, values.position_time, date_options_delta);
        }
        if (values.hasOwnProperty('server_time')) {
            last_server_time = format_time_delta_ago_with_date(current_time, values.server_time, date_options_delta)
        }
        if (values.hasOwnProperty('leader_time_diff')) {
            var total_min = Math.round(values.leader_time_diff / 60);
            var min = total_min % 60;
            var hours = Math.floor(total_min / 60);
            // TODO more than a day
            if (total_min < 60) { leader_time_diff = sprintf(':%02i', min) }
            else { leader_time_diff = sprintf('%i:%02i', hours, min) }
        }
        if (detail_level == 'tracker') {
            return '<tr rider_name="' + rider.name + '" class="rider">' +
                   '<td style="background: ' + (rider.color || 'black') + ';">&nbsp;&nbsp;&nbsp;</td>' +
                   '<td class="name">' + rider.name + '</td>' +
                   (state.live?'<td style="text-align: right">' + (last_position_time || '') + '</td>':'') +
                   '<td style="text-align: right">' + (last_server_time || '') + '</td>' +
                   '<td style="text-align: right">' + (values.battery ? sprintf('%i %%', values.battery) : '') + '</td>' +
                   '<td>' + (values.hasOwnProperty('tk_config')? values.tk_config : '') + '</td>' +
                   '</tr>';
        }
        if (detail_level == 'progress') {
            return '<tr rider_name="' + rider.name + '" class="rider">' +
                   '<td style="background: ' + (rider.color || 'black') + ';">&nbsp;&nbsp;&nbsp;</td>' +
                   '<td class="name">' + rider.name + '</td>' +
                   (state.live?'<td style="text-align: right">' + (last_position_time || '') + '</td>':'') +
                   '<td>' + rider_status + '</td>' +
                   '<td style="text-align: right">' + (current_time - values.position_time < 15 * 60 && values.speed_from_last ? sprintf('%.1f', values.speed_from_last) || '': '') + '</td>' +
                   '<td style="text-align: right">' + (values.hasOwnProperty('dist_route') ? sprintf('%.1f', values.dist_route / 1000) : '') + '</td>' +
                   (expect_off_route?'<td style="text-align: right">' + (values.hasOwnProperty('dist') ? sprintf('%.1f', values.dist / 1000) : '') + '</td>':'') +
                   '<td style="text-align: right">' + leader_time_diff + '</td>' +
                   '<td style="text-align: right">' + (finished_time || '') + '</td>' +
                   '</tr>';
        }
        if (detail_level == 'simple') {
            return '<tr rider_name="' + rider.name + '" class="rider">' +
                   '<td style="background: ' + (rider.color || 'black') + ';">&nbsp;&nbsp;&nbsp;</td>' +
                   '<td class="name">' + rider.name + '</td>' +
                   '<td style="text-align: right">' +
                        (finished_time || (values.hasOwnProperty('dist_route') ? sprintf('%.1f km', values.dist_route / 1000) : ''))
                        + '<br>' + (rider_status || (state.live && last_position_long_ago?last_position_time:'') || '') +'</td>' +
                   '<td style="text-align: right">' + leader_time_diff + '</td>' +
                   '</tr>';
        }
    });
    if (detail_level == 'tracker') {
        container.innerHTML =
            '<table class="riders"><colgroup><col style="width:54px; max-width: 54px;"><col style="width:50%;"></colgroup><tr class="head">' +
            '<td></td>' +
            '<td>Name</td>' +
            '<td style="text-align: right">Last<br>Position</td>' +
            '<td style="text-align: right">Last<br>Connection</td>' +
            '<td>Battery</td>' +
            '<td>Config</td>' +
            '</tr>' + rider_rows.join('') + '</table>';
    }
    if (detail_level == 'progress') {
        container.innerHTML =
            '<table class="riders"><colgroup><col style="width:54px; max-width: 54px;"><col style="width:50%;"></colgroup><tr class="head">' +
            '<td></td>' +
            '<td>Name</td>' +
            (state.live?'<td>Last Position</td>':'') +
            '<td>Status</td>' +
            '<td style="text-align: right">Current<br>Speed</td>' +
            (expect_off_route?'<td style="text-align: right">Dist on<br>Main Route</td>':'') +
            '<td style="text-align: right">Dist</td>' +
            '<td style="text-align: right">Gap to<br>Leader</td>' +
            '<td style="text-align: right">Finish<br>Time</td>' +
            '</tr>' + rider_rows.join('') + '</table>';
    }
    if (detail_level == 'simple') {
        container.innerHTML =
            '<table class="riders"><colgroup><col style="width:54px; max-width: 54px;"><col style="width:50%;"></colgroup>' + rider_rows.join('') + '</table>';
    }
    riders_el = container.querySelectorAll('.rider');
    Array.prototype.forEach.call(riders_el, function (row){
        var rider_name = row.getAttribute('rider_name');
        row.onclick = rider_onclick.bind(null, row, rider_name);
        if (selected_riders.has(rider_name)) row.classList.add('selected');
    });
}

riders_detail_level_el.onchange = update_rider_table;

var selected_riders = new Set();
function rider_onclick(row, rider_name, event) {
    event.preventDefault();
    event.stopPropagation();
    select_rider(rider_name, false, true, event);
//    if (event.ctrlKey) {
//        var values = riders_values[rider_name] || {};
//        if (values.hasOwnProperty('position')) {
//            window.open('https://www.google.com/maps/place/' + values.position[0] + ',' + values.position[1], '_blank');
//        }
//    }
}

function select_rider(rider_name, rider_list_scroll, map_scroll, event) {
    var single = !event.ctrlKey;

    point_info_window.close();
    var old_select_riders = new Set(selected_riders);

    if (selected_riders.has(rider_name)) {
        selected_riders.delete(rider_name);
    } else {
        if (single) {
            selected_riders = new Set([rider_name]);
        } else {
            selected_riders.add(rider_name);
        }
    }

    var any_selected = selected_riders.size > 0;
    config.riders.forEach(function (rider){
        var rider_items = riders_client_items[rider.name];
        var zIndex;
        var opacity;
        var selected = selected_riders.has(rider.name);
        if (any_selected && selected) {
            zIndex = 1000;
            opacity = 1;
        } else if (any_selected && !selected){
            zIndex = 1;
            opacity = 0.5;
        } else {
            zIndex = 1;
            opacity = 1;
        }
        if (rider_items.marker) {
            rider_items.marker.setZIndex(zIndex);
            rider_items.marker.setOpacity(opacity);
        }
        Object.keys(rider_items.paths).forEach(function (list_name) {
            var paths = rider_items.paths[list_name];
            Object.values(paths).forEach(function (path) {
                path.setOptions({zIndex: (list_name == 'riders_points'? zIndex: 0), strokeOpacity: opacity});
            });
        });
        update_rider_paths_visible(rider.name);
        var rows = [
            document.getElementById('riders_actual').querySelector(".rider[rider_name='"+CSS.escape(rider.name)+"']"),
            document.getElementById('graphs_riders').querySelector(".rider[rider_name='"+CSS.escape(rider.name)+"']"),
        ];
        rows.forEach(function (row) {
            if (selected) {
                row.classList.add('selected');
            } else {
                row.classList.remove('selected');
            }
            if (selected && rider.name == rider_name) {
                if (rider_list_scroll) {
                    row.scrollIntoView({'behavior': 'smooth'});
                }
            }
        });
        if (selected && rider.name == rider_name) {
            if (map_scroll && rider_items.marker && last_mobile_selected == 'map') {
                setTimeout(function(){
                    apply_mobile_selected(last_mobile_selected);
                    map.panTo(rider_items.marker.getPosition());
                });
            }
            if (last_mobile_selected == 'graphs') {
                setTimeout(function(){
                    apply_mobile_selected(last_mobile_selected);
                });
            }
        }
    });

    // Removed from selection
    old_select_riders.forEach(function (rider_name){
        if (!selected_riders.has(rider_name)) {
            subscriptions['riders_points.'+rider_name] = Math.max((subscriptions['rider_points.'+rider_name] || 0) - 1, 0);
            on_new_rider_values(rider_name);
        }
    });

    // Added to selection
    selected_riders.forEach(function (rider_name){
        if (!old_select_riders.has(rider_name)) {
            subscriptions['riders_points.'+rider_name] = Math.max((subscriptions['rider_points.'+rider_name] || 0) + 1, 0);
            on_new_rider_values(rider_name);
        }
    });

    event_markers.forEach(function (marker) { if (marker.hasOwnProperty('setOpacity')) {marker.setOpacity((any_selected ? 0.5 : 1))} });
    subscriptions_updated();
    update_selected_rider_point_markers();
    if (desktop_main_selected == 'graphs' || mobile_selected == 'graphs') update_graph();
}

function update_selected_rider_point_markers(){
    config.riders.forEach(function (rider){
        var rider_name = rider.name;
        var rider_items = riders_client_items[rider_name];
        if (!selected_riders.has(rider_name)) {
            Object.values(rider_items.point_markers).forEach(function (marker) {marker.setVisible(false)});
        } else if (riders_points.hasOwnProperty(rider_name)) {
            var bounds = map.getBounds();
            var color = rider.color || 'black';

            if (map.getZoom() >= 14) {
                Object.values(rider_items.point_markers).forEach(function (marker) {marker.setVisible(true)});
                riders_points[rider_name].forEach(function (point){
                    if (!rider_items.point_markers.hasOwnProperty(point.index) && point.hasOwnProperty('position')) {
                        var position = new google.maps.LatLng(point.position[0], point.position[1]);
                        if (bounds.contains(position)){
                            var marker = new google.maps.Marker({
                                icon: {
                                    path: google.maps.SymbolPath.CIRCLE,
                                    scale: 3,
                                    strokeColor: color,
                                    fillColor: color,
                                    fillOpacity: 1,
                                },
                                draggable: false,
                                map: map,
                                position: position,
                            });

                            marker.addListener('click', point_marker_onclick.bind(null, marker, point));
                            rider_items.point_markers[point.index] = marker;
                        }
                    }
                });
            } else {
                Object.values(rider_items.point_markers).forEach(function (marker) {marker.setVisible(false)});
            }
        }
    });
}

function point_marker_onclick(marker, point) {
    var content = '<table>';
    var current_time = (new Date().getTime() / 1000) - time_offset;
    if (point.hasOwnProperty('time')) {
        // TODO more than a day
        var time = new Date(point.time * 1000);
        var rel_time = format_time_delta_ago(current_time - point.time);
        content += sprintf('<tr><td style="font-weight: bold;">Time:</td><td>%s<br>%s</td></tr>',
                           time.toLocaleString(date_locale, date_options), rel_time);
        if (config.hasOwnProperty('event_start')){
            var race_time = format_time_delta(point.time - config.event_start, time_show_days);
            content += sprintf('<tr><td style="font-weight: bold;">Race Time:</td><td>%s</td></tr>', race_time);
        }
    }
    content += sprintf('<tr><td style="font-weight: bold;">Position:</td><td>%.6f, %.6f</td></tr>',
                       point.position[0], point.position[1]);
    if (point.hasOwnProperty('accuracy')) {
        content += sprintf('<tr><td style="font-weight: bold;">Accuracy:</td><td>%.1f m</td></tr>',
                           point.accuracy);
    }
    if (point.position.length == 3) {
        content += sprintf('<tr><td style="font-weight: bold;">Elevation:</td><td>%.0f m</td></tr>',
                           point.position[2]);
    }
    if (point.hasOwnProperty('dist_route')) {
        content += sprintf('<tr><td style="font-weight: bold;">Dist on Route:</td><td>%.1f km</td></tr>',
                           point.dist_route / 1000);
    }
    if (point.hasOwnProperty('dist')) {
        content += sprintf('<tr><td style="font-weight: bold;">Dist Total:</td><td>%.1f km</td></tr>',
                           point.dist / 1000);
    }
    if (point.hasOwnProperty('dist_from_last')) {
        content += sprintf('<tr><td style="font-weight: bold;">Dist from last point:</td><td>%.1f km</td></tr>',
                           point.dist_from_last / 1000);
    }
    if (point.hasOwnProperty('speed_from_last')) {
        content += sprintf('<tr><td style="font-weight: bold;">Speed from last point:</td><td>%.1f km/h</td></tr>',
                           point.speed_from_last );
    }
    if (point.hasOwnProperty('time_from_last')) {
        content += sprintf('<tr><td style="font-weight: bold;">Time from last point:</td><td>%s</td></tr>',
                           format_time_delta(point.time_from_last));
    }
    if (point.hasOwnProperty('server_time')) {
        var time = new Date(point.server_time * 1000);
        var rel_time = format_time_delta_ago(current_time - point.server_time);
        content += sprintf('<tr><td style="font-weight: bold;">Server Time:</td><td>%s<br>%s</td></tr>',
                           time.toLocaleString(date_locale, date_options), rel_time);
        if (point.hasOwnProperty('time')){
            var delay = format_time_delta(point.server_time - point.time);
            content += sprintf('<tr><td style="font-weight: bold;">Delay to server:</td><td>%s</td></tr>', delay);
        }
    }
    if (point.hasOwnProperty('server_time_from_last')) {
        content += sprintf('<tr><td style="font-weight: bold;">Server Time from last point:</td><td>%s</td></tr>',
                           format_time_delta(point.server_time_from_last));
    }
    content += '</table>';
    point_info_window.setContent(content);
    point_info_window.open(map, marker);
    point_info_window.setPosition(marker.position);
}

load_state();

var my_position_el = document.getElementById('my_position');
var geolocation_watch_id;
var my_position_marker;

my_position_el.onclick = function(){
    if (my_position_el.checked && !geolocation_watch_id) {
        geolocation_watch_id = navigator.geolocation.watchPosition(geo_location_success, promise_catch, {enableHighAccuracy: true, });
    }

    if (!my_position_el.checked && geolocation_watch_id) {
        navigator.geolocation.clearWatch(geolocation_watch_id);
        geolocation_watch_id = null;
        if (my_position_marker){
            my_position_marker.setMap(null);
            my_position_marker = null;
        }
    }
}

function geo_location_success(position){
    var map_position = new google.maps.LatLng(position.coords.latitude, position.coords.longitude)
    if (!my_position_marker){
        var marker_html = '<div class="rider-marker" style="background: black; color: white;">Me</div>' +
                          '<div class="rider-marker-pointer" style="border-color: transparent black black transparent;"></div>';
        my_position_marker = new RichMarker({
            map: map,
            position: map_position,
            flat: true,
            content: marker_html
        })
    } else {
        my_position_marker.setPosition(map_position);
    }
}

my_position_el.onclick();

var graph_charts = {};
var graph_selected;
var graphs_block_link_event_state = false;

function graphs_block_link_event(func, event){
    if (!graphs_block_link_event_state) {
        graphs_block_link_event_state = true;
        try {
            func(event);
        } finally {
            graphs_block_link_event_state = false;
        }
    }
}

function mouseover_other_chart(event, other_series){
    var point = event.target;
    var other_chart = other_series.chart;
    var other_index = binary_search_closest(other_series.xData, point.x)
    if (other_index  === undefined){
        other_chart.pointer.reset();
    } else {
        var other_point = other_series.data[other_index];
        other_chart.pointer.runPointActions(event, other_point);
    }
}

function graphs_sync_other_extremes(event) {
    var thisChart = event.target;
    if (event.trigger !== 'syncExtremes') { // Prevent feedback loop
        Object.values(graph_charts).forEach(function (chart) {
            if (chart !== thisChart) {
                var axis = chart.xAxis[0];
                if (axis.setExtremes) { // It is null while updating
                    axis.setExtremes(event.min, event.max, undefined, false, { trigger: 'syncExtremes' });
                }
            }
        });
    }
}


var graph_contain = document.getElementById('graph_contain');
var graph_selected_riders = new Set()

function remove_graph_subscriptions() {
    graph_selected_riders.forEach(function (rider_name) {
        subscriptions['riders_points.'+rider_name] = Math.max((subscriptions['rider_points.'+rider_name] || 0) - 1, 0);
    });
    graph_selected_riders = new Set()
    subscriptions_updated();
}


function update_graph() {
    config_loaded.promise.then( function () {
        var old_graph_selected_riders = graph_selected_riders;
        graph_selected_riders.forEach(function (rider_name) {
            subscriptions['riders_points.'+rider_name] = Math.max((subscriptions['rider_points.'+rider_name] || 0) - 1, 0);
        });
        if (selected_riders.size > 0) {
            graph_selected_riders = new Set(selected_riders);
        } else {
            var sorted_riders = get_rider_values_and_sorted_riders()[1];
            graph_selected_riders = new Set(sorted_riders.slice(0, 10).map(function (rider) { return rider.name; }))
        }
        graph_selected_riders.forEach(function (rider_name) {
            subscriptions['riders_points.'+rider_name] = Math.max((subscriptions['rider_points.'+rider_name] || 0) + 1, 0);
        });
        subscriptions_updated();
        if (graph_selected != graph_select.value || old_graph_selected_riders != graph_selected_riders) {
            Object.values(graph_charts).forEach(function (chart){ chart.destroy(); });
            graph_charts = {}

            graph_selected = graph_select.value;
            if (graph_selected == 'dist_speed_time') {
                graph_contain.innerHTML = '<div id="graph_dist_time"></div><div id="graph_speed_time"></div>';
                graph_charts.dist_time = Highcharts.chart('graph_dist_time', {
                    chart: { type: 'line', height: null, zoomType: 'xy', },
                    title: { text: 'Distance / Time', style: {display: 'none'}},
                    xAxis: {
                        title: 'Time',
                        type: 'datetime',
                        endOnTick: false,
                        startOnTick: false,
                        crosshair: true,
                        events: { setExtremes: graphs_sync_other_extremes },
                    },
                    yAxis: [
                        {
                            title: {text: 'Distance (km)', },
                            id: 'dist',
                            endOnTick: false, startOnTick: false,
                        }
                    ],
                    credits: { enabled: false },
                    legend:{ enabled: false },
                    plotOptions: {
                        series: {
                            point: {
                                events: {
                                    mouseOver: graphs_block_link_event.bind(null, function (event) {
                                        mouseover_other_chart(event, graph_charts.speed_time.series[event.target.series.index], );
                                    }),
                                },
                            },
                            events: {
                                mouseOut: function (event) {
                                    graph_charts.speed_time.pointer.reset();
                                }
                            },
                        },
                    },
                    series: config.riders.filter(function (rider) {return graph_selected_riders.has(rider.name)}).map(function (rider) { return {
                        id: rider['name'],
                        name: rider['name'],
                        color: rider['color'],
                        tooltip: {
                            headerFormat: '<b>' + rider.name +'</b><br>',
                            pointFormat: '{point.x:%H:%M:%S %e %b}: {point.y:.2f} km'
                        },
                    }}),
                });
                graph_charts.speed_time = Highcharts.chart('graph_speed_time', {
                    chart: { type: 'line', height: null, zoomType: 'x', },
                    title: { text: 'Speed / Time', style: {display: 'none'}},
                    xAxis: {
                        title: 'Time',
                        type: 'datetime',
                        endOnTick: false,
                        startOnTick: false,
                        crosshair: true,
                        events: { setExtremes: graphs_sync_other_extremes },
                    },
                    yAxis: [
                        {
                            title: {text: 'Speed (km/h)' },
                            id: 'speed',
                            ceiling: 80,
                        },
                    ],
                    credits: { enabled: false },
                    legend:{ enabled: false },
                    plotOptions: {
                        series: {
                            point: {
                                events: {
                                    mouseOver: graphs_block_link_event.bind(null, function (event) {
                                        mouseover_other_chart(event, graph_charts.speed_time.series[event.target.series.index], );
                                    }),
                                },
                            },
                            events: {
                                mouseOut: function (event) {
                                    graph_charts.dist_time.pointer.reset();
                                }
                            },
                        },
                    },
                    series: config.riders.filter(function (rider) {return graph_selected_riders.has(rider.name)}).map(function (rider) {return {
                        id: rider['name'],
                        name: rider['name'],
                        color: rider['color'],
                        yAxis: 'speed',
                        tooltip: {
                            headerFormat: '<b>' + rider.name +'</b><br>',
                            pointFormat: '{point.x:%H:%M:%S %e %b}: {point.y:.2f} km'
                        },
                    }}),
                });
            }
            if (graph_selected == 'elevation_speed_distance') {
                graph_contain.innerHTML = '<div id="graph_speed_dist"></div><div id="graph_elevation_dist"></div>';

                graph_charts.speed_dist = Highcharts.chart('graph_speed_dist', {
                    chart: { type: 'line', height: null, zoomType: 'x', },
                    title: { text: 'Speed / Distance', style: {display: 'none'}},
                    xAxis: {
                        title: 'Distance',
                        endOnTick: false,
                        startOnTick: false,
                        crosshair: true,
                        events: { setExtremes: graphs_sync_other_extremes },
                    },
                    yAxis: [
                        {
                            title: {text: 'Speed (km/h)' },
                            id: 'speed',
                            ceiling: 80,
                        },
                    ],
                    credits: { enabled: false },
                    legend:{ enabled: false },
                    plotOptions: {
                        series: {
                            point: {
                                events: {
                                    mouseOver: graphs_block_link_event.bind(null, function (event) {
                                        mouseover_other_chart(event, graph_charts.elevation_dist.series[0], );
                                    }),
                                },
                            },
                            events: {
                                mouseOut: function (event) {
                                    graph_charts.elevation_dist.pointer.reset();
                                }
                            },
                        },
                    },
                    series: config.riders.filter(function (rider) {return graph_selected_riders.has(rider.name)}).map(function (rider) {return {
                        id: rider['name'],
                        name: rider['name'],
                        color: rider['color'],
                        tooltip: {
                            headerFormat: '<b>' + rider.name +'</b><br>',
                            pointFormat: '{point.x:.2f} km: {point.y:.2f} km/h',
                        },
                    }}),
                });
                graph_charts.elevation_dist = Highcharts.chart('graph_elevation_dist', {
                    chart: { type: 'line', height: null, zoomType: 'x', },
                    title: { text: 'Elevation Distance', },
                    xAxis: {
                        title: 'Distance',
                        endOnTick: false,
                        startOnTick: false,
                        crosshair: true,
                        events: { setExtremes: graphs_sync_other_extremes },
                    },
                    yAxis: [
                        {
                            title: {text: 'Elevation (m)', },
                            id: 'elev',
                            endOnTick: false, startOnTick: false,
                        },
                    ],
                    credits: { enabled: false },
                    legend:{ enabled: false },
                    series: routes.map(function (route, i) {
                        var start_distance;
                        var dist_factor;
                        if (route.main) {
                            start_distance = 0;
                            dist_factor = 1;
                        } else {
                            start_distance = route.start_distance;
                            dist_factor = route.dist_factor;
                        }

                        return {
                            color: (i==0?'black':'#444444'),
                            tooltip: {
                                headerFormat: '',
                                pointFormat: '{point.x:.2f} km: {point.y:.2f} m',
                            },

                            data: route.elevation.map(function (point) { return [
                                ((point[3] * dist_factor) + start_distance) / 1000,
                                point[2],
                            ]}),
                        }
                    }),
                });

            }
            if (graph_selected == 'battery') {
                graph_contain.innerHTML = '<div id="graph_battery"></div>'

                graph_charts.graph_battery = Highcharts.chart('graph_battery', {
                    chart: { type: 'line', height: null, zoomType: 'xy', },
                    title: { text: 'Tracker Battery Levels / Time', style: {display: 'none'}},
                    xAxis: { title: 'Time', type: 'datetime', endOnTick: false, startOnTick: false, },
                    yAxis: [
                        {
                            title: {text: 'Battery Level (%)', },
                            id: 'battery',
                            max: 100,
                            min: 0,
                            endOnTick: false, startOnTick: false,
                        },
                    ],
                    credits: { enabled: false },
                    legend:{ enabled: false },
                    series: config.riders.filter(function (rider) {return graph_selected_riders.has(rider.name)}).map(function (rider) {return {
                        id: rider['name'],
                        name: rider['name'],
                        color: rider['color'],
                        tooltip: {
                            headerFormat: '<b>' + rider.name +'</b><br>',
                            pointFormat: '{point.x:%H:%M:%S %e %b}: {point.y:.0f} %'
                        },
                    } }),
                });
            }

            graph_selected_riders.forEach(function (rider_name) {
                var rider_points = riders_points[rider_name];
                if (rider_points) on_new_rider_points_graph(rider_name, 'riders_points', rider_points, rider_points, [], false);
            });
            Object.values(graph_charts).forEach(function (chart){ chart.update(); });
        }

    }).catch(promise_catch);
}

var graph_update_timeout;

function on_new_rider_points_graph(rider_name, list_name, items, new_items, old_items, update){
    var applicable_points = (
        list_name == 'riders_points'
        ||
        (list_name == 'riders_pre_post' && pre_post_el.checked && graph_charts.graph_battery)
    )
    if (applicable_points && (mobile_selected == 'graphs' || desktop_main_selected == 'graphs')) {
        if (graph_charts.dist_time && graph_charts.speed_time) {
            if (graph_charts.dist_time.get(rider_name)) graph_charts.dist_time.get(rider_name).setData(
                items.filter(function(item) {return item.hasOwnProperty('dist_route')})
                .map(function (item) {return [item.time * 1000, item.dist_route/1000]})
            );
            if (graph_charts.speed_time.get(rider_name)) graph_charts.speed_time.get(rider_name).setData(
                items.filter(function(item) {return item.hasOwnProperty('speed_from_last')})
                .map(function (item) {return [item.time * 1000, item.speed_from_last || 0]})
            );
        }
        if (graph_charts.speed_dist) {
            if (graph_charts.speed_dist.get(rider_name)) graph_charts.speed_dist.get(rider_name).setData(
                items.filter(function(item) {return item.hasOwnProperty('speed_from_last')})
                .map(function (item) {return [item.dist_route / 1000, item.speed_from_last]})
            );
        }
        if (graph_charts.graph_battery) {
            if (pre_post_el.checked) {
                // Horrible hack
                items = riders_pre_post[rider_name].concat(riders_points[rider_name]);
                items = items.filter(function(item) {return item.hasOwnProperty('battery')});
                items.sort(function (a, b) { return a.time - b.time });
            }

            if (graph_charts.graph_battery.get(rider_name)) graph_charts.graph_battery.get(rider_name).setData(
                items.filter(function(item) {return item.hasOwnProperty('battery')})
                .map(function (item) {return [item.time * 1000, item.battery]})
            );
        }
        if (update && !graph_update_timeout) {
            graph_update_timeout = setTimeout(500, )
        }
    }
}

function update_graphs(){
    graph_update_timeout = null;
    Object.values(graph_charts).forEach(function (chart){ chart.update(); });
}


Highcharts.setOptions({
    time: {
        useUTC: false
    }
});
