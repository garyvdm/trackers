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
}

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
