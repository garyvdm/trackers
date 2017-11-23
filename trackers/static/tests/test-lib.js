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
