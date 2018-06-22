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
var trackers = {}

function ws_onmessage(event){
    set_status('&#x2713; Connected');
    console.log(event.data);

    var data = JSON.parse(event.data);
    if (data.hasOwnProperty('trackers')) {
        trackers = data.trackers;
        update_trackers()
    }
    if (data.hasOwnProperty('values')) {
        values = data.values;
        update_values();
    }
    if (data.hasOwnProperty('changed_values')) {
        Object.assign(values, data.changed_values);
        update_values();
    }


}

function update_trackers() {
    var ids = Object.keys(trackers);
    ids.sort();
    var table_rows = ids.map(function (id) {
        var tracker = trackers[id];

        return '' +
            sprintf('<tr tk_id="%s" >', id) +
            sprintf('<td>%s<br><a href="tel:%s">%s</a><br>%s<br><span id="active"></span></td>', id, tracker.phone_number, tracker.phone_number, tracker.device_id) +
            '<td style="text-align: right"></td>' +
            '<td></td>' +
            '<td></td>' +
            '<td></td>' +
            '<td>' +
            sprintf('<button onclick="send_command(\'%s\', \'*getpos*\', true);">Get Position</button>', id) +
            sprintf('<button onclick="send_command(\'%s\', \'*status*\', true);">Get Status</button>', id) +
            '<br>Config: ' +
            sprintf('<button onclick="set_config(\'%s\', {});">Off</button>', id) +
            sprintf('<button onclick="del_config(\'%s\');">Clear</button>', id) +
            '<br>Routetrack: ' +
            sprintf('<button onclick="set_config(\'%s\', {routetrack: true, rupload: 60, rsampling: 60});">60 sec</button>', id) +
            sprintf('<button onclick="set_config(\'%s\', {routetrack: true, rupload: 10, rsampling: 10});">10 sec</button>', id) +
            '<br>Check: ' +
            sprintf('<button onclick="set_config(\'%s\', {check: 5});">5min</button>', id) +

//            '<br>Routetrack: ' +
//            sprintf('<button onclick="send_command(\'%s\', \'*routetrackoff*\', true);">Off</button>', id) +
//            sprintf('<button onclick="routetrack(\'%s\', 60);">60 sec</button>', id) +
//            sprintf('<button onclick="routetrack(\'%s\', 10);">10 sec</button>', id) +
//            '<br>Check: ' +
//            sprintf('<button onclick="send_command(\'%s\', \'*checkoff*\', true);">Off</button>', id) +
//            sprintf('<button onclick="send_command(\'%s\', \'*checkm*5*\', true);">5min</button>', id) +
//            '<br>' +
//            sprintf('<button onclick="send_command(\'%s\', \'*apn*internet*\', true);">*apn*internet*</button>', id) +
//            sprintf('<button onclick="send_command(\'%s\', \'*master*123456*+27635933475*\', true);">master</button>', id) +
//            sprintf('<button onclick="send_command(\'%s\', \'*multiquery*\', true);">*multiquery*</button>', id) +
//            sprintf('<button onclick="send_command(\'%s\', \'*alertoff*\', true);">alertoff</button>', id) +
//            sprintf('<button onclick="send_command(\'%s\', \'*setip*154*127*61*242*6002*\', true);">setip</button>', id) +
//            sprintf('<button onclick="basic_config(\'%s\');">Basic Config</button>', id) +
            '</td>'+
            '</tr>';
    });
    document.getElementById('trackers').innerHTML =
        '<table><tr class="head">' +
        '<td>Id</td>' +
        '<td>Last<br>Connection</td>' +
        '<td>Position</td>' +
        '<td>Status</td>' +
        '<td>Config</td>' +
        '<td>Actions</td>' +
        '</tr>' + table_rows.join('') + '</table>';
    update_values();
}

function update_values() {
    var now = (new Date().getTime() / 1000);
    Object.keys(values).forEach(function (id) {
        var tk_values = values[id];

        var row = document.querySelector(sprintf('*[tk_id=%s]', id));
        if (!row) return;

        var cells = row.cells;

        if (tk_values.hasOwnProperty('active')) {
            row.querySelector('#active').innerText = Object.keys(tk_values.active).join(', ');
        } else {
            row.querySelector('#active').innerText = '';
        }

        if (tk_values.hasOwnProperty('last_connection')) {
            cells[1].innerHTML = format_time_delta_ago_with_date(now, tk_values.last_connection, date_options)
        } else {
            cells[1].innerText = '';
        }

        if (tk_values.hasOwnProperty('position')) {
            var latlng = sprintf('%.6f,%.6f', tk_values.position.value[0], tk_values.position.value[1]);
            cells[2].innerHTML = sprintf(
                '<a href="http://www.google.com/maps/place/%s" target="blank">%s</a>' +
                '<div class="ago">%s</div>',
                latlng, latlng, format_time_delta_ago_with_date(now, tk_values.position.time, date_options))
        } else {
            cells[2].innerText = '';
        }

        if (tk_values.hasOwnProperty('tk_status')) {
            cells[3].innerHTML = sprintf(
                '%s<div class="ago">%s</div>', tk_values.tk_status.value.replace(/\r\n/g, '<br>'),
                format_time_delta_ago_with_date(now, tk_values.tk_status.time, date_options))
        } else {
            cells[3].innerText = '';
        }

        var config_cell = ''
        if (tk_values.hasOwnProperty('tk_config')) {
            config_cell += sprintf(
                '%s<div class="ago">%s</div>', tk_values.tk_config.value,
                format_time_delta_ago_with_date(now, tk_values.tk_config.time, date_options))
        }
        if (tk_values.hasOwnProperty('desired_configs')) {
            Object.keys(tk_values.desired_configs).forEach(function (config_id){
                config_cell += sprintf('<div>%s rank=%s</div>', config_id, tk_values.desired_configs[config_id].rank)
            });
        }
        cells[4].innerHTML = config_cell
    });
}

function basic_config(id){
    send_commands(id, [
            '*master*123456*+27635933475*',
            '*apn*internet*',
            '*multiquery*',
            '*setip*154*127*61*242*6002*',
        ], true);
}

function routetrack(id, time){
    send_commands(id, [
            '*routetrack*99*',
            sprintf('*rupload*%s*', time),
            sprintf('*rsampling*%s*', time)
        ], 'first');
}

function send_command(id, command, urgent){
    send_commands(id, [command], urgent);
}

function send_commands(id, commands, urgent){
    var data = JSON.stringify({'commands': commands, 'id': id, 'urgent': urgent});
    ws.send(data);
}

function set_config(id, config){
    var data = JSON.stringify({'config': config, 'id': id});
    ws.send(data);
}

function del_config(id){
    var data = JSON.stringify({'del_config': true, 'id': id});
    ws.send(data);
}

ws_ensure_connect();
setInterval(update_values, 1000);
