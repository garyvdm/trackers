"use strict";

var loader_html = '<span class="l1"></span><span class="l2"></span><span class="l3"></span> '

function get(url) {
    return fetch(location.pathname + url).then( function(response) { return response.json() }, function(error) { throw error });
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
    if (state.live) {
        window.localStorage.setItem(location.pathname, JSON.stringify(state));
    } else {
        window.localStorage.removeItem(location.pathname);
    }
}

function get_state(){
    get('/state').then(on_new_state_received_non_ws);
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
}

var state = {}
var config;
var routes = []
var all_route_points = [];

var event_markers = [];
var route_paths = [];
var riders_by_name = {};
var riders_client_items = {};
var riders_points = {};

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
    }
    if (new_state.hasOwnProperty('config_hash') && state.config_hash != new_state.config_hash) {
        state.config_hash = new_state.config_hash;
        get('/config?hash=' + new_state.config_hash).then(function (new_config){
            config = new_config;
            on_new_config();
            config_loaded.resolve()
        }).catch(promise_catch);
        need_save = true;
    }
    if (new_state.hasOwnProperty('routes_hash') && state.routes_hash != new_state.routes_hash) {
        state.routes_hash = new_state.routes_hash;
        get('/routes?hash=' + state.routes_hash).then(function (new_routes){
            routes = new_routes;
            on_new_routes();
        }).catch(promise_catch);
        need_save = true;
    }
    if (new_state.hasOwnProperty('riders_points')) {
        Object.entries(new_state.riders_points).forEach(function (entry){
            var name = entry[0];
            var update = entry[1];

            var rider_points = riders_points[name] || [];

            function fetch_block(block) {
                return get('/rider_points?name=' + name + '&start_index=' + block.start_index +
                           '&end_index=' + block.end_index + '&end_hash=' + block.end_hash);
            }

            process_update_list(fetch_block, rider_points, update).then(function (rider_points) {
                riders_points[name] = rider_points.new_list;
                on_new_rider_points(name, rider_points.new_list, rider_points.new_items, rider_points.old_items);
            });
        });

    }
    if (state.hasOwnProperty('riders_points')) {
        delete state.riders_points;
        need_save = true;
    }

    if (need_save) save_state(state);
}


var ws;
var close_reason;
var reconnect_time = 1000;

var time_offset = 0;

var ws_connection_wanted = false;


function ws_ensure_connect(){
    ws_connection_wanted = true;
    if (ws_connection_wanted && !ws) {
        set_status(loader_html + 'Connecting');
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
}

function reconnect_status(time){
    set_status(close_reason + '<br>Reconnecting in ' + Math.floor((reconnect_time - time) / 1000) + ' sec.');
}

function ws_onclose(event) {
    ws = null;
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
    // console.log(event.data);

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

load_state();

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

var config_loaded = new Deferred();
var map;
var route_marker;

var elevation_chart = Highcharts.chart('elevation', {
    chart: { type: 'line', height: null },
    title: { text: 'Elevation', style: {display: 'none'} },
    legend:{ enabled: false },
    xAxis: { id: 'xAris', type: 'linear',
        labels: { formatter: function () { return (Math.round(this.value / 100) / 10).toString() + " km"; }}
    },
    yAxis: { title: {text: null}, endOnTick: false, startOnTick: false, labels: {format: '{value} m'} },
    credits: { enabled: false },
    tooltip: {
        formatter: function() {
            return (Math.round(this.x / 100) / 10).toString() + " km : " +  Math.round(this.y).toString() + ' m';
        }
    },
    series: [],
});


var race_time = document.getElementById('race_time');
setInterval(function(){
    if (config && config.hasOwnProperty('event_start')){
        race_time.innerText = 'Race time: ' + format_time_delta((new Date().getTime() / 1000) - config.event_start - time_offset);
    } else {
        race_time.innerHTML = '&nbsp;';
    }
}, 1000);

function on_new_config(){
    event_markers.forEach(function (marker) { marker.setMap(null) });
    event_markers = [];
    Object.keys(riders_client_items).forEach(on_clear_rider_points);

    if (config) {
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
            var marker = new google.maps.Marker(marker_data);
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

        riders_by_name = {};
        config.riders.forEach(function (rider) {
            riders_by_name[rider.name] = rider
            on_new_rider_points(rider.name, [], [], [])
        });

        update_rider_table();
    }
}

var route_marker;


function on_new_routes(){
    config_loaded.promise.then( function () {
        route_paths.forEach(function (path) { path.setMap(null) });
        elevation_chart.series.forEach(function (series) { series.remove(false) });

        route_paths = routes.map(function (route){
            return new google.maps.Polyline({
                map: map,
                path: route.points.map(function (point) {return new google.maps.LatLng(point[0], point[1])}),
                geodesic: false,
                strokeColor: 'black',
                strokeOpacity: 0.7,
                strokeWeight: 2,
                zIndex: -1
            })
        });

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

            elevation_chart.addSeries({
                marker: {enabled: false, symbol: 'circle'},
                color: 'black',
                turboThreshold: 5000,
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
                }

            }, false);
        });
        elevation_chart.redraw(false);
        adjust_elevation_chart_bounds();
    });
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

function on_clear_rider_points(rider_name){
    if (riders_client_items.hasOwnProperty(rider_name)) {
        var rider_items = riders_client_items[rider_name]
        Object.values(rider_items.paths || {}).forEach(function (path){ path.setMap(null) });
        if (rider_items.marker) rider_items.marker.setMap(null);
        // TODO the following errors on chrome. Need to fix
        if (rider_items.elevation_chart_series) rider_items.elevation_chart_series.remove(false);
        delete riders_client_items[rider_name];
    }
}

function on_new_rider_points(rider_name, items, new_items, old_items){
    config_loaded.promise.then( function () {

        if (old_items.length) {
            on_clear_rider_points(rider_name);
            new_items = items;
        }

        var rider = riders_by_name[rider_name]
        if (!rider) return;
        var rider_items = riders_client_items[rider_name] || (riders_client_items[rider_name] = {
            paths: {},
            marker: null,
            elevation_chart_series: null,
            current_values: {},
            last_position_point: null,
            position_point: null,
        });
        var path_color = rider.color || 'black';
        var rider_current_values = rider_items.current_values;

        new_items.forEach(function (point) {
            if (point.hasOwnProperty('position')) {
                var path = (rider_items.paths[point.track_id] || (rider_items.paths[point.track_id] = new google.maps.Polyline({
                    map: map,
                    path: [],
                    geodesic: false,
                    strokeColor: path_color,
                    strokeOpacity: 1.0,
                    strokeWeight: 2
                }))).getPath()
                path.push(new google.maps.LatLng(point.position[0], point.position[1]));
                rider_items.last_position_point = rider_items.position_point;
                rider_items.position_point = point;
            }
            Object.assign(rider_current_values, point);
        });
        var marker_color = rider.color_marker || 'white';
        var marker_html = '<div class="rider-marker" style="background: ' + marker_color + ';">' + (rider.name_short || rider.name)+ '</div>' +
                          '<div class="rider-marker-pointer" style="border-color: transparent ' + marker_color + ' ' + marker_color + ' transparent;"></div>'

        if (rider_items.position_point) {
            var position = new google.maps.LatLng(rider_items.position_point.position[0], rider_items.position_point.position[1])
            if (!rider_items.marker) {
                rider_items.marker = new RichMarker({
                    map: map,
                    position: position,
                    flat: true,
                    content: marker_html
                })
            } else {
                rider_items.marker.setPosition(position);
            }
        }
        if (rider_items.position_point && rider_items.position_point.hasOwnProperty('dist_route')) {
            if (!rider_items.elevation_chart_series) {
                rider_items.elevation_chart_series = elevation_chart.addSeries({
                    marker: { symbol: 'circle'},
                    color: marker_color,
                    data: [],
                }, false);
            }
            var elevation = null;
            if (rider_items.position_point && rider_items.position_point.position.length > 2) {
                elevation = rider_items.position_point.position[2]
            } else if (routes) {
                // TODO the server analyse tracker should do this.
                elevation = binarySearchClosest(
                    routes[0].elevation,
                    rider_items.position_point.dist_route,
                    function (point) { return point[3] }  // [3] = distance
                )[2];  // [2] = elevation
            } else {
                elevation = 0;
            }
            rider_items.elevation_chart_series.setData([{
                x: rider_current_values['dist_route'],
                y: elevation,
                dataLabels: {
                    enabled: true,
                    format: rider.name_short || rider.name,
                    allowOverlap: true,
                    shape: 'callout',
                    backgroundColor: marker_color,
                    style: {
                        textOutline: 'none'
                    }
                },
            }], false, false, false)
        }
        update_rider_table();
        elevation_chart.redraw(false);
    });
}

var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
var riders_detail_el = document.getElementById('riders_detail');
var riders_el = [];
function update_rider_table(){
    if (config) {
        var sorted_riders = config.riders.slice();
        sorted_riders.sort(function (a, b){
            var a_rider_items = riders_client_items[a.name] || {};
            var a_current_values = a_rider_items.current_values || {};
            var b_rider_items = riders_client_items[b.name] || {};
            var b_current_values = b_rider_items.current_values || {};

            if (a_current_values.finished_time && !b_current_values.finished_time || a_current_values.finished_time < b_current_values.finished_time) return -1;
            if (!a_current_values.finished_time && b_current_values.finished_time || a_current_values.finished_time > b_current_values.finished_time) return 1;

            if (a_current_values.dist_route && !b_current_values.dist_route || a_current_values.dist_route > b_current_values.dist_route) return -1;
            if (!a_current_values.dist_route && b_current_values.dist_route || a_current_values.dist_route < b_current_values.dist_route) return 1;
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
                if (config && config.hasOwnProperty('event_start')){
                    finished_time = format_time_delta(current_values.finished_time - config.event_start);
                } else {
                    var time = new Date(current_values.finished_time * 1000);
                    finished_time = sprintf('%s %02i:%02i:%02i', days[time.getDay()], time.getHours(), time.getMinutes(), time.getSeconds() )
                }

            }
            if (position_point) {
                // TODO more than a day
                var seconds = current_time - position_point.time;
                if (seconds < 60) { last_position_time = '< 1 min ago' }
                else if (seconds < 60 * 60) { last_position_time = sprintf('%i min ago', Math.floor(seconds / 60))}
                else { last_position_time = sprintf('%i:%02i ago', Math.floor(seconds / 60 / 60), Math.floor(seconds / 60 % 60))}
            }
            if (position_point && last_position_point && position_point.hasOwnProperty('dist_ridden') && last_position_point.hasOwnProperty('dist_ridden') && current_values.status == 'Active') {
                speed = Math.round((position_point.dist_ridden - last_position_point.dist_ridden) / (position_point.time - last_position_point.time) * 3.6 * 10) /10
            }
            if (show_detail) {
                return '<tr rider_name="' + rider.name + '" class="rider">' +
                       '<td style="background: ' + (rider.color || 'black') + '">&nbsp;&nbsp;&nbsp;</td>' +
                       '<td>' + rider.name + '</td>' +
                       '<td>' + rider_status + '</td>' +
                       '<td>' + (current_values.status || '') + '</td>' +
                       '<td style="text-align: right">' +  (last_position_time || '') + '</td>' +
//                           '<td style="text-align: right">' + (current_values.hasOwnProperty('dist_ridden') ? sprintf('%.1f', current_values.dist_ridden / 1000) : '') + '</td>' +
                       '<td style="text-align: right">' + (speed || '') + '</td>' +
                       '<td style="text-align: right">' + (current_values.hasOwnProperty('dist_route') ? sprintf('%.1f', current_values.dist_route / 1000) : '') + '</td>' +
                       '<td style="text-align: right">' + (finished_time || '') + '</td>' +
                       '</tr>';
            } else {
                return '<tr rider_name="' + rider.name + '" class="rider">' +
                       '<td style="background: ' + (rider.color || 'black') + '">&nbsp;&nbsp;&nbsp;</td>' +
                       '<td>' + rider.name + '</td>' +
                       '<td style="text-align: right">' + (finished_time || (current_values.hasOwnProperty('dist_route') ? sprintf('%.1f km', current_values.dist_route / 1000) : '')) + ' ' + rider_status +'</td>' +
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
    config.riders.forEach(function (rider){
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
            window.open('https://www.google.com/maps/place/' + position_point.position[0] + ',' + position_point.position[1], '_blank');
        }
    }
}


function binarySearchClosest(arr, searchElement, key) {

  var minIndex = 0;
  var maxIndex = arr.length - 1;
  var currentIndex;
  var currentElement;

  while (minIndex <= maxIndex) {
      currentIndex = (minIndex + maxIndex) / 2 | 0;
      currentElement = key(arr[currentIndex]);
      nextElement = key(arr[currentIndex + 1]);

      if (searchElement > currentElement && searchElement < nextElement ) {
          return currentIndex;
      } else if (currentElement < searchElement) {
          minIndex = currentIndex + 1;
      }
      else if (currentElement > searchElement) {
          maxIndex = currentIndex - 1;
      }
  }

  return -1;
}
