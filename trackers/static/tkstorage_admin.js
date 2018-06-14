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

var ws;
var ws_connected = false;
var close_reason;
var reconnect_time = 1000;

function ws_ensure_connect(){
    if (!ws) {
        set_status(loader_html + 'Connecting');
        if (!window.WebSocket) {
            document.getElementById('badbrowser').display = 'block';
            log_to_server('No WebSocket support');
        }
        ws = new WebSocket(location.protocol.replace('http', 'ws') + '//' + location.host + '/tkstorage_admin/tkstorage_websocket');
        ws.onopen = ws_onopen;
        ws.onclose = ws_onclose;
        ws.onmessage = ws_onmessage;
    }
}

function ws_close(){
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
}

function reconnect_status(time){
    set_status(close_reason + '<br>Reconnecting in ' + Math.floor((reconnect_time - time) / 1000) + ' sec.');
}

function ws_onclose(event) {
    ws = null;
    ws_connected = false;
    if (event.reason.startsWith('Server Error:')) {
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

var values = {};


function ws_onmessage(event){
    set_status('&#x2713; Connected');
    console.log(event.data);

    var data = JSON.parse(event.data);
    if (data.hasOwnProperty('values')) {
        values = data.values;
        update_values();
    }
    if (data.hasOwnProperty('changed_values')) {
        Object.assign(values, new_values);
        update_values();
    }
}

ws_ensure_connect();


function update_values() {
    var now = (new Date().getTime() / 1000);
    var ids = Object.keys(values);
    ids.sort();
    var table_rows = ids.map(function (id) {
        var tk_values = values[id];

        var position = ''
        if (tk_values.hasOwnProperty('position')) {
            var latlng = sprintf('%.6f,%.6f', tk_values.position.value[0], tk_values.position.value[1]);
            var position = sprintf('<a href="http://www.google.com/maps/place/%s" target="blank">%s</a>' +
                                   '<div class="ago">%s</div>',
                                   latlng, latlng, format_time_delta_ago(now - tk_values.position.time))
        }

        var tk_status = ''
        if (tk_values.hasOwnProperty('tk_status')) {
            var tk_status = sprintf('%s<div class="ago">%s</div>', tk_values.tk_status.value.replace(/\r\n/g, '<br>'),
                                    format_time_delta_ago(now - tk_values.tk_status.time))
        }

        var tk_config = ''
        if (tk_values.hasOwnProperty('tk_config')) {
            var tk_config = sprintf('%s<div class="ago">%s</div>', tk_values.tk_config.value,
                                    format_time_delta_ago(now - tk_values.tk_config.time))
        }

        return '' +
            sprintf('<tr tk_id="%s" >', id) +
            sprintf('<td>%s</td>', id) +
            sprintf('<td style="text-align: right">%s</td>', format_time_delta_ago(now - tk_values.last_connection)) +
            sprintf('<td>%s</td>', position) +
            sprintf('<td>%s</td>', tk_status) +
            sprintf('<td>%s</td>', tk_config) +

           '</tr>';
    });
    document.getElementById('trackers').innerHTML =
        '<table><tr class="head">' +
        '<td>Id</td>' +
        '<td>Last<br>Connection</td>' +
        '<td>Position</td>' +
        '<td>Status</td>' +
        '<td>Config</td>' +
        '</tr>' + table_rows.join('') + '</table>';
}
