import assert from 'node:assert/strict';
import {test} from 'node:test';
import {buildConfiguration, describeState, sameScale} from '../display-config.mjs';

function fixture(scale = 1) {
    return [
        42,
        [[
            ['DP-3', 'vendor', 'product', 'serial'],
            [
                ['4k-current', 3840, 2160, 59.997, 1, [1, 1.25, 1.5, 2, 3], {'is-current': true}],
                ['1080p', 1920, 1080, 60, 1, [1, 2], {}],
            ],
            {'is-underscanning': false, 'color-mode': 2},
        ]],
        [[0, 0, scale, 1, true, [['DP-3', 'vendor', 'product', 'serial']], {}]],
        {'layout-mode': 1, 'supports-changing-layout-mode': true},
    ];
}

test('switching between 100% and 200% preserves the display mode and properties', () => {
    for (const [before, after] of [[1, 2], [2, 1]]) {
        const state = describeState(fixture(before));
        const configuration = buildConfiguration(state, after);
        assert.equal(configuration.serial, 42);
        assert.deepEqual(configuration.logicalMonitors, [[
            0, 0, after, 1, true,
            [['DP-3', '4k-current', {underscanning: false, 'color-mode': 2}]],
        ]]);
        assert.deepEqual(configuration.properties, {'layout-mode': 1});
        assert.equal(state.scale, before);
    }
});

test('supported fractional scales use the precise compositor value', () => {
    const raw = fixture();
    raw[1][0][1][0][5].push(1.3333333730697632);
    const configuration = buildConfiguration(describeState(raw), 4 / 3);
    assert.equal(configuration.logicalMonitors[0][2], 1.3333333730697632);
    assert.ok(sameScale(1.3333333730697632, 4 / 3));
});

test('unsupported scales and missing modes fail explicitly', () => {
    assert.throws(() => buildConfiguration(describeState(fixture()), 4), /not supported/);
    const raw = fixture();
    raw[1][0][1][0][6] = {};
    assert.throws(() => describeState(raw), /current mode/);
});

test('extended desktops are rejected without changing their arrangement', () => {
    const raw = fixture();
    raw[2].push([3840, 0, 1, 0, false, [['DP-4', 'v', 'p', 's']], {}]);
    assert.throws(() => describeState(raw), /extended desktops/);
    assert.equal(raw[2][1][0], 3840);
});

test('mirrored displays offer only their shared supported scales', () => {
    const raw = fixture();
    raw[1].push([
        ['HDMI-1', 'other', 'display', 'serial'],
        [['mirror-mode', 3840, 2160, 60, 1, [1, 2], {'is-current': true}]],
        {},
    ]);
    raw[2][0][5].push(raw[1][1][0]);
    const state = describeState(raw);
    assert.deepEqual(state.scales, [1, 2]);
    assert.equal(buildConfiguration(state, 2).logicalMonitors[0][5].length, 2);
});

test('layout-mode is not sent when the compositor cannot change it', () => {
    const raw = fixture();
    raw[3]['supports-changing-layout-mode'] = false;
    assert.deepEqual(buildConfiguration(describeState(raw), 2).properties, {});
});

test('leased outputs remain leased without being activated', () => {
    const raw = fixture();
    const spec = ['DP-4', 'vr-vendor', 'headset', 'serial'];
    raw[1].push([spec, [], {'is-for-lease': true}]);
    const configuration = buildConfiguration(describeState(raw), 2);
    assert.deepEqual(configuration.properties['monitors-for-lease'], [spec]);
    assert.equal(configuration.logicalMonitors[0][5].length, 1);
});
