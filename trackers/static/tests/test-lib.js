if (location.hash == '#post_results') {
    QUnit.done( function(details) {
        var request = new XMLHttpRequest();
        request.open("POST", '/results', true);
        request.send(JSON.stringify(details));
        if (window.__coverage__) {
            var request = new XMLHttpRequest();
            request.open("POST", '/coverage', true);
            request.send(JSON.stringify(window.__coverage__));
        }
    } );
    QUnit.testDone( function(details) {
        var request = new XMLHttpRequest();
        request.open("POST", '/log', true);
        request.send(JSON.stringify(details));
    } );
    function log(msg){
        console.log(msg);
        var request = new XMLHttpRequest();
        request.open("POST", '/log', true);
        request.send(JSON.stringify(msg));
    }
} else {
    log = console.log
}


QUnit.module( "lib" );

QUnit.test( 'format_time_delta', function( assert ) {
    assert.equal(
        format_time_delta(1000000),
        '277:46:40'
    );
});

QUnit.test( 'format_time_delta show_days', function( assert ) {
    assert.equal(
        format_time_delta(1000000, show_days=true),
        '11d 13:46:40'
    );
});


QUnit.test( 'format_time_delta_ago lt 1 min', function( assert ) {
    assert.equal(
        format_time_delta_ago(45),
        '< 1 min ago'
    );
});


QUnit.test( 'format_time_delta_ago 2 min', function( assert ) {
    assert.equal(
        format_time_delta_ago(125),
        '2 min ago'
    );
});

QUnit.test( 'format_time_delta_ago gt 1 hour', function( assert ) {
    assert.equal(
        format_time_delta_ago(3660),
        '1:01 ago'
    );
});
