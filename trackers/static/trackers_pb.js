/*eslint-disable block-scoped-var, no-redeclare, no-control-regex, no-prototype-builtins*/
(function($protobuf) {
    "use strict";

    var $Reader = $protobuf.Reader, $util = $protobuf.util;
    
    var $root = $protobuf.roots["default"] || ($protobuf.roots["default"] = {});
    
    $root.trackers = (function() {
    
        var trackers = {};
    
        trackers.Position = (function() {
    
            function Position(p) {
                if (p)
                    for (var ks = Object.keys(p), i = 0; i < ks.length; ++i)
                        if (p[ks[i]] != null)
                            this[ks[i]] = p[ks[i]];
            }
    
            Position.prototype.lat = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
            Position.prototype.lng = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
            Position.prototype.elevation = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
    
            Position.decode = function decode(r, l) {
                if (!(r instanceof $Reader))
                    r = $Reader.create(r);
                var c = l === undefined ? r.len : r.pos + l, m = new $root.trackers.Position();
                while (r.pos < c) {
                    var t = r.uint32();
                    switch (t >>> 3) {
                    case 1:
                        m.lat = r.sint64();
                        break;
                    case 2:
                        m.lng = r.sint64();
                        break;
                    case 3:
                        m.elevation = r.int64();
                        break;
                    default:
                        r.skipType(t & 7);
                        break;
                    }
                }
                if (!m.hasOwnProperty("lat"))
                    throw $util.ProtocolError("missing required 'lat'", { instance: m });
                if (!m.hasOwnProperty("lng"))
                    throw $util.ProtocolError("missing required 'lng'", { instance: m });
                return m;
            };
    
            Position.fromObject = function fromObject(d) {
                if (d instanceof $root.trackers.Position)
                    return d;
                var m = new $root.trackers.Position();
                if (d.lat != null) {
                    if ($util.Long)
                        (m.lat = $util.Long.fromValue(d.lat)).unsigned = false;
                    else if (typeof d.lat === "string")
                        m.lat = parseInt(d.lat, 10);
                    else if (typeof d.lat === "number")
                        m.lat = d.lat;
                    else if (typeof d.lat === "object")
                        m.lat = new $util.LongBits(d.lat.low >>> 0, d.lat.high >>> 0).toNumber();
                }
                if (d.lng != null) {
                    if ($util.Long)
                        (m.lng = $util.Long.fromValue(d.lng)).unsigned = false;
                    else if (typeof d.lng === "string")
                        m.lng = parseInt(d.lng, 10);
                    else if (typeof d.lng === "number")
                        m.lng = d.lng;
                    else if (typeof d.lng === "object")
                        m.lng = new $util.LongBits(d.lng.low >>> 0, d.lng.high >>> 0).toNumber();
                }
                if (d.elevation != null) {
                    if ($util.Long)
                        (m.elevation = $util.Long.fromValue(d.elevation)).unsigned = false;
                    else if (typeof d.elevation === "string")
                        m.elevation = parseInt(d.elevation, 10);
                    else if (typeof d.elevation === "number")
                        m.elevation = d.elevation;
                    else if (typeof d.elevation === "object")
                        m.elevation = new $util.LongBits(d.elevation.low >>> 0, d.elevation.high >>> 0).toNumber();
                }
                return m;
            };
    
            Position.toObject = function toObject(m, o) {
                if (!o)
                    o = {};
                var d = {};
                if (o.defaults) {
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.lat = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.lat = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.lng = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.lng = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.elevation = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.elevation = o.longs === String ? "0" : 0;
                }
                if (m.lat != null && m.hasOwnProperty("lat")) {
                    if (typeof m.lat === "number")
                        d.lat = o.longs === String ? String(m.lat) : m.lat;
                    else
                        d.lat = o.longs === String ? $util.Long.prototype.toString.call(m.lat) : o.longs === Number ? new $util.LongBits(m.lat.low >>> 0, m.lat.high >>> 0).toNumber() : m.lat;
                }
                if (m.lng != null && m.hasOwnProperty("lng")) {
                    if (typeof m.lng === "number")
                        d.lng = o.longs === String ? String(m.lng) : m.lng;
                    else
                        d.lng = o.longs === String ? $util.Long.prototype.toString.call(m.lng) : o.longs === Number ? new $util.LongBits(m.lng.low >>> 0, m.lng.high >>> 0).toNumber() : m.lng;
                }
                if (m.elevation != null && m.hasOwnProperty("elevation")) {
                    if (typeof m.elevation === "number")
                        d.elevation = o.longs === String ? String(m.elevation) : m.elevation;
                    else
                        d.elevation = o.longs === String ? $util.Long.prototype.toString.call(m.elevation) : o.longs === Number ? new $util.LongBits(m.elevation.low >>> 0, m.elevation.high >>> 0).toNumber() : m.elevation;
                }
                return d;
            };
    
            Position.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };
    
            return Position;
        })();
    
        trackers.Point = (function() {
    
            function Point(p) {
                if (p)
                    for (var ks = Object.keys(p), i = 0; i < ks.length; ++i)
                        if (p[ks[i]] != null)
                            this[ks[i]] = p[ks[i]];
            }
    
            Point.prototype.index = $util.Long ? $util.Long.fromBits(0,0,true) : 0;
            Point.prototype.time = $util.Long ? $util.Long.fromBits(0,0,true) : 0;
            Point.prototype.time_from_last = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
            Point.prototype.server_time = $util.Long ? $util.Long.fromBits(0,0,true) : 0;
            Point.prototype.server_time_from_last = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
            Point.prototype.position = null;
            Point.prototype.route_elevation = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
            Point.prototype.track_id = 0;
            Point.prototype.dist = $util.Long ? $util.Long.fromBits(0,0,true) : 0;
            Point.prototype.dist_route = $util.Long ? $util.Long.fromBits(0,0,true) : 0;
            Point.prototype.dist_from_last = $util.Long ? $util.Long.fromBits(0,0,true) : 0;
            Point.prototype.num_sat = 0;
            Point.prototype.speed_from_last = 0;
            Point.prototype.config = "";
            Point.prototype.rider_status = "";
            Point.prototype.finished_time = $util.Long ? $util.Long.fromBits(0,0,true) : 0;
    
            Point.decode = function decode(r, l) {
                if (!(r instanceof $Reader))
                    r = $Reader.create(r);
                var c = l === undefined ? r.len : r.pos + l, m = new $root.trackers.Point();
                while (r.pos < c) {
                    var t = r.uint32();
                    switch (t >>> 3) {
                    case 1:
                        m.index = r.uint64();
                        break;
                    case 2:
                        m.time = r.uint64();
                        break;
                    case 3:
                        m.time_from_last = r.int64();
                        break;
                    case 4:
                        m.server_time = r.uint64();
                        break;
                    case 5:
                        m.server_time_from_last = r.int64();
                        break;
                    case 6:
                        m.position = $root.trackers.Position.decode(r, r.uint32());
                        break;
                    case 7:
                        m.route_elevation = r.int64();
                        break;
                    case 8:
                        m.track_id = r.uint32();
                        break;
                    case 9:
                        m.dist = r.uint64();
                        break;
                    case 10:
                        m.dist_route = r.uint64();
                        break;
                    case 11:
                        m.dist_from_last = r.uint64();
                        break;
                    case 12:
                        m.num_sat = r.uint32();
                        break;
                    case 13:
                        m.speed_from_last = r.float();
                        break;
                    case 14:
                        m.config = r.string();
                        break;
                    case 15:
                        m.rider_status = r.string();
                        break;
                    case 16:
                        m.finished_time = r.uint64();
                        break;
                    default:
                        r.skipType(t & 7);
                        break;
                    }
                }
                return m;
            };
    
            Point.fromObject = function fromObject(d) {
                if (d instanceof $root.trackers.Point)
                    return d;
                var m = new $root.trackers.Point();
                if (d.index != null) {
                    if ($util.Long)
                        (m.index = $util.Long.fromValue(d.index)).unsigned = true;
                    else if (typeof d.index === "string")
                        m.index = parseInt(d.index, 10);
                    else if (typeof d.index === "number")
                        m.index = d.index;
                    else if (typeof d.index === "object")
                        m.index = new $util.LongBits(d.index.low >>> 0, d.index.high >>> 0).toNumber(true);
                }
                if (d.time != null) {
                    if ($util.Long)
                        (m.time = $util.Long.fromValue(d.time)).unsigned = true;
                    else if (typeof d.time === "string")
                        m.time = parseInt(d.time, 10);
                    else if (typeof d.time === "number")
                        m.time = d.time;
                    else if (typeof d.time === "object")
                        m.time = new $util.LongBits(d.time.low >>> 0, d.time.high >>> 0).toNumber(true);
                }
                if (d.time_from_last != null) {
                    if ($util.Long)
                        (m.time_from_last = $util.Long.fromValue(d.time_from_last)).unsigned = false;
                    else if (typeof d.time_from_last === "string")
                        m.time_from_last = parseInt(d.time_from_last, 10);
                    else if (typeof d.time_from_last === "number")
                        m.time_from_last = d.time_from_last;
                    else if (typeof d.time_from_last === "object")
                        m.time_from_last = new $util.LongBits(d.time_from_last.low >>> 0, d.time_from_last.high >>> 0).toNumber();
                }
                if (d.server_time != null) {
                    if ($util.Long)
                        (m.server_time = $util.Long.fromValue(d.server_time)).unsigned = true;
                    else if (typeof d.server_time === "string")
                        m.server_time = parseInt(d.server_time, 10);
                    else if (typeof d.server_time === "number")
                        m.server_time = d.server_time;
                    else if (typeof d.server_time === "object")
                        m.server_time = new $util.LongBits(d.server_time.low >>> 0, d.server_time.high >>> 0).toNumber(true);
                }
                if (d.server_time_from_last != null) {
                    if ($util.Long)
                        (m.server_time_from_last = $util.Long.fromValue(d.server_time_from_last)).unsigned = false;
                    else if (typeof d.server_time_from_last === "string")
                        m.server_time_from_last = parseInt(d.server_time_from_last, 10);
                    else if (typeof d.server_time_from_last === "number")
                        m.server_time_from_last = d.server_time_from_last;
                    else if (typeof d.server_time_from_last === "object")
                        m.server_time_from_last = new $util.LongBits(d.server_time_from_last.low >>> 0, d.server_time_from_last.high >>> 0).toNumber();
                }
                if (d.position != null) {
                    if (typeof d.position !== "object")
                        throw TypeError(".trackers.Point.position: object expected");
                    m.position = $root.trackers.Position.fromObject(d.position);
                }
                if (d.route_elevation != null) {
                    if ($util.Long)
                        (m.route_elevation = $util.Long.fromValue(d.route_elevation)).unsigned = false;
                    else if (typeof d.route_elevation === "string")
                        m.route_elevation = parseInt(d.route_elevation, 10);
                    else if (typeof d.route_elevation === "number")
                        m.route_elevation = d.route_elevation;
                    else if (typeof d.route_elevation === "object")
                        m.route_elevation = new $util.LongBits(d.route_elevation.low >>> 0, d.route_elevation.high >>> 0).toNumber();
                }
                if (d.track_id != null) {
                    m.track_id = d.track_id >>> 0;
                }
                if (d.dist != null) {
                    if ($util.Long)
                        (m.dist = $util.Long.fromValue(d.dist)).unsigned = true;
                    else if (typeof d.dist === "string")
                        m.dist = parseInt(d.dist, 10);
                    else if (typeof d.dist === "number")
                        m.dist = d.dist;
                    else if (typeof d.dist === "object")
                        m.dist = new $util.LongBits(d.dist.low >>> 0, d.dist.high >>> 0).toNumber(true);
                }
                if (d.dist_route != null) {
                    if ($util.Long)
                        (m.dist_route = $util.Long.fromValue(d.dist_route)).unsigned = true;
                    else if (typeof d.dist_route === "string")
                        m.dist_route = parseInt(d.dist_route, 10);
                    else if (typeof d.dist_route === "number")
                        m.dist_route = d.dist_route;
                    else if (typeof d.dist_route === "object")
                        m.dist_route = new $util.LongBits(d.dist_route.low >>> 0, d.dist_route.high >>> 0).toNumber(true);
                }
                if (d.dist_from_last != null) {
                    if ($util.Long)
                        (m.dist_from_last = $util.Long.fromValue(d.dist_from_last)).unsigned = true;
                    else if (typeof d.dist_from_last === "string")
                        m.dist_from_last = parseInt(d.dist_from_last, 10);
                    else if (typeof d.dist_from_last === "number")
                        m.dist_from_last = d.dist_from_last;
                    else if (typeof d.dist_from_last === "object")
                        m.dist_from_last = new $util.LongBits(d.dist_from_last.low >>> 0, d.dist_from_last.high >>> 0).toNumber(true);
                }
                if (d.num_sat != null) {
                    m.num_sat = d.num_sat >>> 0;
                }
                if (d.speed_from_last != null) {
                    m.speed_from_last = Number(d.speed_from_last);
                }
                if (d.config != null) {
                    m.config = String(d.config);
                }
                if (d.rider_status != null) {
                    m.rider_status = String(d.rider_status);
                }
                if (d.finished_time != null) {
                    if ($util.Long)
                        (m.finished_time = $util.Long.fromValue(d.finished_time)).unsigned = true;
                    else if (typeof d.finished_time === "string")
                        m.finished_time = parseInt(d.finished_time, 10);
                    else if (typeof d.finished_time === "number")
                        m.finished_time = d.finished_time;
                    else if (typeof d.finished_time === "object")
                        m.finished_time = new $util.LongBits(d.finished_time.low >>> 0, d.finished_time.high >>> 0).toNumber(true);
                }
                return m;
            };
    
            Point.toObject = function toObject(m, o) {
                if (!o)
                    o = {};
                var d = {};
                if (o.defaults) {
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, true);
                        d.index = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.index = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, true);
                        d.time = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.time = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.time_from_last = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.time_from_last = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, true);
                        d.server_time = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.server_time = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.server_time_from_last = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.server_time_from_last = o.longs === String ? "0" : 0;
                    d.position = null;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.route_elevation = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.route_elevation = o.longs === String ? "0" : 0;
                    d.track_id = 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, true);
                        d.dist = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.dist = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, true);
                        d.dist_route = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.dist_route = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, true);
                        d.dist_from_last = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.dist_from_last = o.longs === String ? "0" : 0;
                    d.num_sat = 0;
                    d.speed_from_last = 0;
                    d.config = "";
                    d.rider_status = "";
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, true);
                        d.finished_time = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.finished_time = o.longs === String ? "0" : 0;
                }
                if (m.index != null && m.hasOwnProperty("index")) {
                    if (typeof m.index === "number")
                        d.index = o.longs === String ? String(m.index) : m.index;
                    else
                        d.index = o.longs === String ? $util.Long.prototype.toString.call(m.index) : o.longs === Number ? new $util.LongBits(m.index.low >>> 0, m.index.high >>> 0).toNumber(true) : m.index;
                }
                if (m.time != null && m.hasOwnProperty("time")) {
                    if (typeof m.time === "number")
                        d.time = o.longs === String ? String(m.time) : m.time;
                    else
                        d.time = o.longs === String ? $util.Long.prototype.toString.call(m.time) : o.longs === Number ? new $util.LongBits(m.time.low >>> 0, m.time.high >>> 0).toNumber(true) : m.time;
                }
                if (m.time_from_last != null && m.hasOwnProperty("time_from_last")) {
                    if (typeof m.time_from_last === "number")
                        d.time_from_last = o.longs === String ? String(m.time_from_last) : m.time_from_last;
                    else
                        d.time_from_last = o.longs === String ? $util.Long.prototype.toString.call(m.time_from_last) : o.longs === Number ? new $util.LongBits(m.time_from_last.low >>> 0, m.time_from_last.high >>> 0).toNumber() : m.time_from_last;
                }
                if (m.server_time != null && m.hasOwnProperty("server_time")) {
                    if (typeof m.server_time === "number")
                        d.server_time = o.longs === String ? String(m.server_time) : m.server_time;
                    else
                        d.server_time = o.longs === String ? $util.Long.prototype.toString.call(m.server_time) : o.longs === Number ? new $util.LongBits(m.server_time.low >>> 0, m.server_time.high >>> 0).toNumber(true) : m.server_time;
                }
                if (m.server_time_from_last != null && m.hasOwnProperty("server_time_from_last")) {
                    if (typeof m.server_time_from_last === "number")
                        d.server_time_from_last = o.longs === String ? String(m.server_time_from_last) : m.server_time_from_last;
                    else
                        d.server_time_from_last = o.longs === String ? $util.Long.prototype.toString.call(m.server_time_from_last) : o.longs === Number ? new $util.LongBits(m.server_time_from_last.low >>> 0, m.server_time_from_last.high >>> 0).toNumber() : m.server_time_from_last;
                }
                if (m.position != null && m.hasOwnProperty("position")) {
                    d.position = $root.trackers.Position.toObject(m.position, o);
                }
                if (m.route_elevation != null && m.hasOwnProperty("route_elevation")) {
                    if (typeof m.route_elevation === "number")
                        d.route_elevation = o.longs === String ? String(m.route_elevation) : m.route_elevation;
                    else
                        d.route_elevation = o.longs === String ? $util.Long.prototype.toString.call(m.route_elevation) : o.longs === Number ? new $util.LongBits(m.route_elevation.low >>> 0, m.route_elevation.high >>> 0).toNumber() : m.route_elevation;
                }
                if (m.track_id != null && m.hasOwnProperty("track_id")) {
                    d.track_id = m.track_id;
                }
                if (m.dist != null && m.hasOwnProperty("dist")) {
                    if (typeof m.dist === "number")
                        d.dist = o.longs === String ? String(m.dist) : m.dist;
                    else
                        d.dist = o.longs === String ? $util.Long.prototype.toString.call(m.dist) : o.longs === Number ? new $util.LongBits(m.dist.low >>> 0, m.dist.high >>> 0).toNumber(true) : m.dist;
                }
                if (m.dist_route != null && m.hasOwnProperty("dist_route")) {
                    if (typeof m.dist_route === "number")
                        d.dist_route = o.longs === String ? String(m.dist_route) : m.dist_route;
                    else
                        d.dist_route = o.longs === String ? $util.Long.prototype.toString.call(m.dist_route) : o.longs === Number ? new $util.LongBits(m.dist_route.low >>> 0, m.dist_route.high >>> 0).toNumber(true) : m.dist_route;
                }
                if (m.dist_from_last != null && m.hasOwnProperty("dist_from_last")) {
                    if (typeof m.dist_from_last === "number")
                        d.dist_from_last = o.longs === String ? String(m.dist_from_last) : m.dist_from_last;
                    else
                        d.dist_from_last = o.longs === String ? $util.Long.prototype.toString.call(m.dist_from_last) : o.longs === Number ? new $util.LongBits(m.dist_from_last.low >>> 0, m.dist_from_last.high >>> 0).toNumber(true) : m.dist_from_last;
                }
                if (m.num_sat != null && m.hasOwnProperty("num_sat")) {
                    d.num_sat = m.num_sat;
                }
                if (m.speed_from_last != null && m.hasOwnProperty("speed_from_last")) {
                    d.speed_from_last = o.json && !isFinite(m.speed_from_last) ? String(m.speed_from_last) : m.speed_from_last;
                }
                if (m.config != null && m.hasOwnProperty("config")) {
                    d.config = m.config;
                }
                if (m.rider_status != null && m.hasOwnProperty("rider_status")) {
                    d.rider_status = m.rider_status;
                }
                if (m.finished_time != null && m.hasOwnProperty("finished_time")) {
                    if (typeof m.finished_time === "number")
                        d.finished_time = o.longs === String ? String(m.finished_time) : m.finished_time;
                    else
                        d.finished_time = o.longs === String ? $util.Long.prototype.toString.call(m.finished_time) : o.longs === Number ? new $util.LongBits(m.finished_time.low >>> 0, m.finished_time.high >>> 0).toNumber(true) : m.finished_time;
                }
                return d;
            };
    
            Point.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };
    
            return Point;
        })();
    
        trackers.Points = (function() {
    
            function Points(p) {
                this.points = [];
                if (p)
                    for (var ks = Object.keys(p), i = 0; i < ks.length; ++i)
                        if (p[ks[i]] != null)
                            this[ks[i]] = p[ks[i]];
            }
    
            Points.prototype.points = $util.emptyArray;
    
            Points.decode = function decode(r, l) {
                if (!(r instanceof $Reader))
                    r = $Reader.create(r);
                var c = l === undefined ? r.len : r.pos + l, m = new $root.trackers.Points();
                while (r.pos < c) {
                    var t = r.uint32();
                    switch (t >>> 3) {
                    case 1:
                        if (!(m.points && m.points.length))
                            m.points = [];
                        m.points.push($root.trackers.Point.decode(r, r.uint32()));
                        break;
                    default:
                        r.skipType(t & 7);
                        break;
                    }
                }
                return m;
            };
    
            Points.fromObject = function fromObject(d) {
                if (d instanceof $root.trackers.Points)
                    return d;
                var m = new $root.trackers.Points();
                if (d.points) {
                    if (!Array.isArray(d.points))
                        throw TypeError(".trackers.Points.points: array expected");
                    m.points = [];
                    for (var i = 0; i < d.points.length; ++i) {
                        if (typeof d.points[i] !== "object")
                            throw TypeError(".trackers.Points.points: object expected");
                        m.points[i] = $root.trackers.Point.fromObject(d.points[i]);
                    }
                }
                return m;
            };
    
            Points.toObject = function toObject(m, o) {
                if (!o)
                    o = {};
                var d = {};
                if (o.arrays || o.defaults) {
                    d.points = [];
                }
                if (m.points && m.points.length) {
                    d.points = [];
                    for (var j = 0; j < m.points.length; ++j) {
                        d.points[j] = $root.trackers.Point.toObject(m.points[j], o);
                    }
                }
                return d;
            };
    
            Points.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };
    
            return Points;
        })();
    
        trackers.RouteElevationPoint = (function() {
    
            function RouteElevationPoint(p) {
                if (p)
                    for (var ks = Object.keys(p), i = 0; i < ks.length; ++i)
                        if (p[ks[i]] != null)
                            this[ks[i]] = p[ks[i]];
            }
    
            RouteElevationPoint.prototype.lat = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
            RouteElevationPoint.prototype.lng = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
            RouteElevationPoint.prototype.elevation = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
            RouteElevationPoint.prototype.distance = $util.Long ? $util.Long.fromBits(0,0,true) : 0;
    
            RouteElevationPoint.decode = function decode(r, l) {
                if (!(r instanceof $Reader))
                    r = $Reader.create(r);
                var c = l === undefined ? r.len : r.pos + l, m = new $root.trackers.RouteElevationPoint();
                while (r.pos < c) {
                    var t = r.uint32();
                    switch (t >>> 3) {
                    case 1:
                        m.lat = r.sint64();
                        break;
                    case 2:
                        m.lng = r.sint64();
                        break;
                    case 3:
                        m.elevation = r.int64();
                        break;
                    case 4:
                        m.distance = r.uint64();
                        break;
                    default:
                        r.skipType(t & 7);
                        break;
                    }
                }
                if (!m.hasOwnProperty("lat"))
                    throw $util.ProtocolError("missing required 'lat'", { instance: m });
                if (!m.hasOwnProperty("lng"))
                    throw $util.ProtocolError("missing required 'lng'", { instance: m });
                if (!m.hasOwnProperty("elevation"))
                    throw $util.ProtocolError("missing required 'elevation'", { instance: m });
                if (!m.hasOwnProperty("distance"))
                    throw $util.ProtocolError("missing required 'distance'", { instance: m });
                return m;
            };
    
            RouteElevationPoint.fromObject = function fromObject(d) {
                if (d instanceof $root.trackers.RouteElevationPoint)
                    return d;
                var m = new $root.trackers.RouteElevationPoint();
                if (d.lat != null) {
                    if ($util.Long)
                        (m.lat = $util.Long.fromValue(d.lat)).unsigned = false;
                    else if (typeof d.lat === "string")
                        m.lat = parseInt(d.lat, 10);
                    else if (typeof d.lat === "number")
                        m.lat = d.lat;
                    else if (typeof d.lat === "object")
                        m.lat = new $util.LongBits(d.lat.low >>> 0, d.lat.high >>> 0).toNumber();
                }
                if (d.lng != null) {
                    if ($util.Long)
                        (m.lng = $util.Long.fromValue(d.lng)).unsigned = false;
                    else if (typeof d.lng === "string")
                        m.lng = parseInt(d.lng, 10);
                    else if (typeof d.lng === "number")
                        m.lng = d.lng;
                    else if (typeof d.lng === "object")
                        m.lng = new $util.LongBits(d.lng.low >>> 0, d.lng.high >>> 0).toNumber();
                }
                if (d.elevation != null) {
                    if ($util.Long)
                        (m.elevation = $util.Long.fromValue(d.elevation)).unsigned = false;
                    else if (typeof d.elevation === "string")
                        m.elevation = parseInt(d.elevation, 10);
                    else if (typeof d.elevation === "number")
                        m.elevation = d.elevation;
                    else if (typeof d.elevation === "object")
                        m.elevation = new $util.LongBits(d.elevation.low >>> 0, d.elevation.high >>> 0).toNumber();
                }
                if (d.distance != null) {
                    if ($util.Long)
                        (m.distance = $util.Long.fromValue(d.distance)).unsigned = true;
                    else if (typeof d.distance === "string")
                        m.distance = parseInt(d.distance, 10);
                    else if (typeof d.distance === "number")
                        m.distance = d.distance;
                    else if (typeof d.distance === "object")
                        m.distance = new $util.LongBits(d.distance.low >>> 0, d.distance.high >>> 0).toNumber(true);
                }
                return m;
            };
    
            RouteElevationPoint.toObject = function toObject(m, o) {
                if (!o)
                    o = {};
                var d = {};
                if (o.defaults) {
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.lat = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.lat = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.lng = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.lng = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.elevation = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.elevation = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, true);
                        d.distance = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.distance = o.longs === String ? "0" : 0;
                }
                if (m.lat != null && m.hasOwnProperty("lat")) {
                    if (typeof m.lat === "number")
                        d.lat = o.longs === String ? String(m.lat) : m.lat;
                    else
                        d.lat = o.longs === String ? $util.Long.prototype.toString.call(m.lat) : o.longs === Number ? new $util.LongBits(m.lat.low >>> 0, m.lat.high >>> 0).toNumber() : m.lat;
                }
                if (m.lng != null && m.hasOwnProperty("lng")) {
                    if (typeof m.lng === "number")
                        d.lng = o.longs === String ? String(m.lng) : m.lng;
                    else
                        d.lng = o.longs === String ? $util.Long.prototype.toString.call(m.lng) : o.longs === Number ? new $util.LongBits(m.lng.low >>> 0, m.lng.high >>> 0).toNumber() : m.lng;
                }
                if (m.elevation != null && m.hasOwnProperty("elevation")) {
                    if (typeof m.elevation === "number")
                        d.elevation = o.longs === String ? String(m.elevation) : m.elevation;
                    else
                        d.elevation = o.longs === String ? $util.Long.prototype.toString.call(m.elevation) : o.longs === Number ? new $util.LongBits(m.elevation.low >>> 0, m.elevation.high >>> 0).toNumber() : m.elevation;
                }
                if (m.distance != null && m.hasOwnProperty("distance")) {
                    if (typeof m.distance === "number")
                        d.distance = o.longs === String ? String(m.distance) : m.distance;
                    else
                        d.distance = o.longs === String ? $util.Long.prototype.toString.call(m.distance) : o.longs === Number ? new $util.LongBits(m.distance.low >>> 0, m.distance.high >>> 0).toNumber(true) : m.distance;
                }
                return d;
            };
    
            RouteElevationPoint.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };
    
            return RouteElevationPoint;
        })();
    
        trackers.RoutePoint = (function() {
    
            function RoutePoint(p) {
                if (p)
                    for (var ks = Object.keys(p), i = 0; i < ks.length; ++i)
                        if (p[ks[i]] != null)
                            this[ks[i]] = p[ks[i]];
            }
    
            RoutePoint.prototype.lat = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
            RoutePoint.prototype.lng = $util.Long ? $util.Long.fromBits(0,0,false) : 0;
    
            RoutePoint.decode = function decode(r, l) {
                if (!(r instanceof $Reader))
                    r = $Reader.create(r);
                var c = l === undefined ? r.len : r.pos + l, m = new $root.trackers.RoutePoint();
                while (r.pos < c) {
                    var t = r.uint32();
                    switch (t >>> 3) {
                    case 1:
                        m.lat = r.sint64();
                        break;
                    case 2:
                        m.lng = r.sint64();
                        break;
                    default:
                        r.skipType(t & 7);
                        break;
                    }
                }
                if (!m.hasOwnProperty("lat"))
                    throw $util.ProtocolError("missing required 'lat'", { instance: m });
                if (!m.hasOwnProperty("lng"))
                    throw $util.ProtocolError("missing required 'lng'", { instance: m });
                return m;
            };
    
            RoutePoint.fromObject = function fromObject(d) {
                if (d instanceof $root.trackers.RoutePoint)
                    return d;
                var m = new $root.trackers.RoutePoint();
                if (d.lat != null) {
                    if ($util.Long)
                        (m.lat = $util.Long.fromValue(d.lat)).unsigned = false;
                    else if (typeof d.lat === "string")
                        m.lat = parseInt(d.lat, 10);
                    else if (typeof d.lat === "number")
                        m.lat = d.lat;
                    else if (typeof d.lat === "object")
                        m.lat = new $util.LongBits(d.lat.low >>> 0, d.lat.high >>> 0).toNumber();
                }
                if (d.lng != null) {
                    if ($util.Long)
                        (m.lng = $util.Long.fromValue(d.lng)).unsigned = false;
                    else if (typeof d.lng === "string")
                        m.lng = parseInt(d.lng, 10);
                    else if (typeof d.lng === "number")
                        m.lng = d.lng;
                    else if (typeof d.lng === "object")
                        m.lng = new $util.LongBits(d.lng.low >>> 0, d.lng.high >>> 0).toNumber();
                }
                return m;
            };
    
            RoutePoint.toObject = function toObject(m, o) {
                if (!o)
                    o = {};
                var d = {};
                if (o.defaults) {
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.lat = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.lat = o.longs === String ? "0" : 0;
                    if ($util.Long) {
                        var n = new $util.Long(0, 0, false);
                        d.lng = o.longs === String ? n.toString() : o.longs === Number ? n.toNumber() : n;
                    } else
                        d.lng = o.longs === String ? "0" : 0;
                }
                if (m.lat != null && m.hasOwnProperty("lat")) {
                    if (typeof m.lat === "number")
                        d.lat = o.longs === String ? String(m.lat) : m.lat;
                    else
                        d.lat = o.longs === String ? $util.Long.prototype.toString.call(m.lat) : o.longs === Number ? new $util.LongBits(m.lat.low >>> 0, m.lat.high >>> 0).toNumber() : m.lat;
                }
                if (m.lng != null && m.hasOwnProperty("lng")) {
                    if (typeof m.lng === "number")
                        d.lng = o.longs === String ? String(m.lng) : m.lng;
                    else
                        d.lng = o.longs === String ? $util.Long.prototype.toString.call(m.lng) : o.longs === Number ? new $util.LongBits(m.lng.low >>> 0, m.lng.high >>> 0).toNumber() : m.lng;
                }
                return d;
            };
    
            RoutePoint.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };
    
            return RoutePoint;
        })();
    
        trackers.Route = (function() {
    
            function Route(p) {
                this.elevation = [];
                this.points = [];
                if (p)
                    for (var ks = Object.keys(p), i = 0; i < ks.length; ++i)
                        if (p[ks[i]] != null)
                            this[ks[i]] = p[ks[i]];
            }
    
            Route.prototype.elevation = $util.emptyArray;
            Route.prototype.points = $util.emptyArray;
            Route.prototype.main = false;
            Route.prototype.dist_factor = 0;
            Route.prototype.start_distance = 0;
            Route.prototype.end_distance = 0;
    
            Route.decode = function decode(r, l) {
                if (!(r instanceof $Reader))
                    r = $Reader.create(r);
                var c = l === undefined ? r.len : r.pos + l, m = new $root.trackers.Route();
                while (r.pos < c) {
                    var t = r.uint32();
                    switch (t >>> 3) {
                    case 1:
                        if (!(m.elevation && m.elevation.length))
                            m.elevation = [];
                        m.elevation.push($root.trackers.RouteElevationPoint.decode(r, r.uint32()));
                        break;
                    case 2:
                        if (!(m.points && m.points.length))
                            m.points = [];
                        m.points.push($root.trackers.RoutePoint.decode(r, r.uint32()));
                        break;
                    case 3:
                        m.main = r.bool();
                        break;
                    case 4:
                        m.dist_factor = r.float();
                        break;
                    case 5:
                        m.start_distance = r.float();
                        break;
                    case 6:
                        m.end_distance = r.float();
                        break;
                    default:
                        r.skipType(t & 7);
                        break;
                    }
                }
                if (!m.hasOwnProperty("main"))
                    throw $util.ProtocolError("missing required 'main'", { instance: m });
                return m;
            };
    
            Route.fromObject = function fromObject(d) {
                if (d instanceof $root.trackers.Route)
                    return d;
                var m = new $root.trackers.Route();
                if (d.elevation) {
                    if (!Array.isArray(d.elevation))
                        throw TypeError(".trackers.Route.elevation: array expected");
                    m.elevation = [];
                    for (var i = 0; i < d.elevation.length; ++i) {
                        if (typeof d.elevation[i] !== "object")
                            throw TypeError(".trackers.Route.elevation: object expected");
                        m.elevation[i] = $root.trackers.RouteElevationPoint.fromObject(d.elevation[i]);
                    }
                }
                if (d.points) {
                    if (!Array.isArray(d.points))
                        throw TypeError(".trackers.Route.points: array expected");
                    m.points = [];
                    for (var i = 0; i < d.points.length; ++i) {
                        if (typeof d.points[i] !== "object")
                            throw TypeError(".trackers.Route.points: object expected");
                        m.points[i] = $root.trackers.RoutePoint.fromObject(d.points[i]);
                    }
                }
                if (d.main != null) {
                    m.main = Boolean(d.main);
                }
                if (d.dist_factor != null) {
                    m.dist_factor = Number(d.dist_factor);
                }
                if (d.start_distance != null) {
                    m.start_distance = Number(d.start_distance);
                }
                if (d.end_distance != null) {
                    m.end_distance = Number(d.end_distance);
                }
                return m;
            };
    
            Route.toObject = function toObject(m, o) {
                if (!o)
                    o = {};
                var d = {};
                if (o.arrays || o.defaults) {
                    d.elevation = [];
                    d.points = [];
                }
                if (o.defaults) {
                    d.main = false;
                    d.dist_factor = 0;
                    d.start_distance = 0;
                    d.end_distance = 0;
                }
                if (m.elevation && m.elevation.length) {
                    d.elevation = [];
                    for (var j = 0; j < m.elevation.length; ++j) {
                        d.elevation[j] = $root.trackers.RouteElevationPoint.toObject(m.elevation[j], o);
                    }
                }
                if (m.points && m.points.length) {
                    d.points = [];
                    for (var j = 0; j < m.points.length; ++j) {
                        d.points[j] = $root.trackers.RoutePoint.toObject(m.points[j], o);
                    }
                }
                if (m.main != null && m.hasOwnProperty("main")) {
                    d.main = m.main;
                }
                if (m.dist_factor != null && m.hasOwnProperty("dist_factor")) {
                    d.dist_factor = o.json && !isFinite(m.dist_factor) ? String(m.dist_factor) : m.dist_factor;
                }
                if (m.start_distance != null && m.hasOwnProperty("start_distance")) {
                    d.start_distance = o.json && !isFinite(m.start_distance) ? String(m.start_distance) : m.start_distance;
                }
                if (m.end_distance != null && m.hasOwnProperty("end_distance")) {
                    d.end_distance = o.json && !isFinite(m.end_distance) ? String(m.end_distance) : m.end_distance;
                }
                return d;
            };
    
            Route.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };
    
            return Route;
        })();
    
        trackers.Routes = (function() {
    
            function Routes(p) {
                this.routes = [];
                if (p)
                    for (var ks = Object.keys(p), i = 0; i < ks.length; ++i)
                        if (p[ks[i]] != null)
                            this[ks[i]] = p[ks[i]];
            }
    
            Routes.prototype.routes = $util.emptyArray;
    
            Routes.decode = function decode(r, l) {
                if (!(r instanceof $Reader))
                    r = $Reader.create(r);
                var c = l === undefined ? r.len : r.pos + l, m = new $root.trackers.Routes();
                while (r.pos < c) {
                    var t = r.uint32();
                    switch (t >>> 3) {
                    case 1:
                        if (!(m.routes && m.routes.length))
                            m.routes = [];
                        m.routes.push($root.trackers.Route.decode(r, r.uint32()));
                        break;
                    default:
                        r.skipType(t & 7);
                        break;
                    }
                }
                return m;
            };
    
            Routes.fromObject = function fromObject(d) {
                if (d instanceof $root.trackers.Routes)
                    return d;
                var m = new $root.trackers.Routes();
                if (d.routes) {
                    if (!Array.isArray(d.routes))
                        throw TypeError(".trackers.Routes.routes: array expected");
                    m.routes = [];
                    for (var i = 0; i < d.routes.length; ++i) {
                        if (typeof d.routes[i] !== "object")
                            throw TypeError(".trackers.Routes.routes: object expected");
                        m.routes[i] = $root.trackers.Route.fromObject(d.routes[i]);
                    }
                }
                return m;
            };
    
            Routes.toObject = function toObject(m, o) {
                if (!o)
                    o = {};
                var d = {};
                if (o.arrays || o.defaults) {
                    d.routes = [];
                }
                if (m.routes && m.routes.length) {
                    d.routes = [];
                    for (var j = 0; j < m.routes.length; ++j) {
                        d.routes[j] = $root.trackers.Route.toObject(m.routes[j], o);
                    }
                }
                return d;
            };
    
            Routes.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };
    
            return Routes;
        })();
    
        return trackers;
    })();

    return $root;
})(protobuf);
