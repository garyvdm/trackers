QUnit.module( "blocked-list" );

blocked_list = {
    'source': [
        {'hash': 'u0Zw', 'index': 0, 'x': 'L'},
        {'hash': '3KI5', 'index': 1, 'x': 'o'},
        {'hash': 'fhi8', 'index': 2, 'x': 'r'},
        {'hash': 'k6sn', 'index': 3, 'x': 'e'},
        {'hash': '96Jb', 'index': 4, 'x': 'm'},
        {'hash': 'q2Yu', 'index': 5, 'x': ' '},
        {'hash': 'agiy', 'index': 6, 'x': 'i'},
        {'hash': 'cTTc', 'index': 7, 'x': 'p'},
        {'hash': 'xtbb', 'index': 8, 'x': 's'},
        {'hash': 'cuBt', 'index': 9, 'x': 'u'},
        {'hash': 'KCgI', 'index': 10, 'x': 'm'},
        {'hash': 'GkCH', 'index': 11, 'x': ' '},
        {'hash': 'bjmL', 'index': 12, 'x': 'd'},
        {'hash': '4a1o', 'index': 13, 'x': 'o'},
        {'hash': '9-EB', 'index': 14, 'x': 'l'},
        {'hash': '9xom', 'index': 15, 'x': 'o'},
        {'hash': 'NrrA', 'index': 16, 'x': 'r'},
        {'hash': 'Wltt', 'index': 17, 'x': ' '},
        {'hash': 'tvhv', 'index': 18, 'x': 's'},
        {'hash': 'qRd1', 'index': 19, 'x': 'i'},
        {'hash': 'zE1X', 'index': 20, 'x': 't'},
        {'hash': 'j3ii', 'index': 21, 'x': ' '},
        {'hash': '-3UE', 'index': 22, 'x': 'a'},
        {'hash': 'yBJf', 'index': 23, 'x': 'm'},
        {'hash': 'juYX', 'index': 24, 'x': 'e'},
        {'hash': 'wARg', 'index': 25, 'x': 't'},
        {'hash': 'Zwue', 'index': 26, 'x': ' '},
        {'hash': 'QFbp', 'index': 27, 'x': 'p'},
        {'hash': 'Vj-2', 'index': 28, 'x': 'o'},
        {'hash': 'k9VG', 'index': 29, 'x': 's'},
        {'hash': 'LaqB', 'index': 30, 'x': 'u'},
        {'hash': '7is4', 'index': 31, 'x': 'e'},
        {'hash': 'yG0W', 'index': 32, 'x': 'r'},
        {'hash': 'qmpi', 'index': 33, 'x': 'e'},
        {'hash': 'opj8', 'index': 34, 'x': '.'}
    ],
    'expected_full': {
        'blocks': [{'end_hash': 'qRd1', 'end_index': 19, 'start_index': 0},
                   {'end_hash': 'juYX', 'end_index': 24, 'start_index': 20},
                   {'end_hash': 'k9VG', 'end_index': 29, 'start_index': 25}],
        'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                          {'hash': '7is4', 'index': 31, 'x': 'e'},
                          {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                          {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                          {'hash': 'opj8', 'index': 34, 'x': '.'}]
    }
};

function test_fetch_block(source, block){
    return new Promise(function(resolve, reject) {
        resolve(source.slice(block.start_index, block.end_index + 1));
    });
}

function check_process_update_list(assert, source, update, old_list, expected_new_list, expected_old_items, expected_new_items) {
    assert.timeout(500);
    var done = assert.async();
    process_update_list(test_fetch_block.bind(null, source), old_list, update).then(function (result) {
        assert.deepEqual(result.new_list, expected_new_list, 'new_list');
        assert.deepEqual(result.old_items, expected_old_items, 'old_items');
        assert.deepEqual(result.new_items, expected_new_items, 'new_items');
        done();
    });
}


QUnit.test('empty_source', function( assert ) {
    var source = [];
    var update = {'blocks': [], 'partial_block': []};
    var old_list = [];

    var expected_new_list = [];
    var expected_old_items = [];
    var expected_new_items = [];

    check_process_update_list(assert, source, update, old_list, expected_new_list, expected_old_items, expected_new_items);
});


QUnit.test('empty_old', function( assert ) {
    var source = blocked_list.source;
    var update = blocked_list.expected_full;
    var old_list = [];

    var expected_new_list = blocked_list.source;
    var expected_old_items = [];
    var expected_new_items = blocked_list.source;

    check_process_update_list(assert, source, update, old_list, expected_new_list, expected_old_items, expected_new_items);
});


QUnit.test('full_old', function( assert ) {
    var source = blocked_list.source;
    var update = blocked_list.expected_full;
    var old_list = blocked_list.source;

    var expected_new_list = blocked_list.source;
    var expected_old_items = [];
    var expected_new_items = [];

    check_process_update_list(assert, source, update, old_list, expected_new_list, expected_old_items, expected_new_items);
});


QUnit.test('partial_old', function( assert ) {
    var source = blocked_list.source;
    var update = blocked_list.expected_full;
    var old_list = blocked_list.source.slice(0, 25);

    var expected_new_list = blocked_list.source;
    var expected_old_items = [];
    var expected_new_items = blocked_list.source.slice(25, 35);

    check_process_update_list(assert, source, update, old_list, expected_new_list, expected_old_items, expected_new_items);
});


QUnit.test('extra_old', function( assert ) {
    var source = blocked_list.source;
    var update = blocked_list.expected_full;
    var old_list = blocked_list.source.concat([{'hash': 'FOOBAR', 'index': 35, 'x': 'l'}]);

    var expected_new_list = blocked_list.source;
    var expected_old_items = [{'hash': 'FOOBAR', 'index': 35, 'x': 'l'}];
    var expected_new_items = [];

    check_process_update_list(assert, source, update, old_list, expected_new_list, expected_old_items, expected_new_items);
});


QUnit.test('changed_old', function( assert ) {
    var source = blocked_list.source;
    var update = blocked_list.expected_full;
    var old_list = blocked_list.source.slice(0, 34).concat([{'hash': 'FOOBAR', 'index': 34, 'x': '.'}]);

    var expected_new_list = blocked_list.source;
    var expected_old_items = [{'hash': 'FOOBAR', 'index': 34, 'x': '.'}];
    var expected_new_items = [{'hash': 'opj8', 'index': 34, 'x': '.'}];

    check_process_update_list(assert, source, update, old_list, expected_new_list, expected_old_items, expected_new_items);
});


QUnit.test('just partial_block update', function( assert ) {
    var source = [{'hash': 'FOOBAR', 'index': 0, 'x': '.'}];
    var update = {'partial_block': source};
    var old_list = [];

    var expected_new_list = source;
    var expected_old_items = [];
    var expected_new_items = source;

    check_process_update_list(assert, source, update, old_list, expected_new_list, expected_old_items, expected_new_items);
});


QUnit.test('add_block', function( assert ) {
    var source = blocked_list.source;
    var update = {'add_block': blocked_list.source.slice(31, 35)};
    var old_list = blocked_list.source.slice(0, 31);

    var expected_new_list = blocked_list.source;
    var expected_old_items = [];
    var expected_new_items = blocked_list.source.slice(31, 35);

    check_process_update_list(assert, source, update, old_list, expected_new_list, expected_old_items, expected_new_items);
});


QUnit.test('bad_format', function( assert ) {
    var source = [];
    var update = {'bad_format': null};
    var old_list = [];
    assert.timeout(500);
    assert.expect(0)

    var done = assert.async();
    process_update_list(test_fetch_block.bind(null, source), old_list, update).catch(function (error) {
        done();
    });});

