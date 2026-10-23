export const sameScale = (left, right) => Math.abs(left - right) < 0.0001;
export const percent = scale => `${Math.round(scale * 100)}%`;

export function describeState(raw) {
    const [serial, monitors, logicalMonitors, properties] = raw;
    if (logicalMonitors.length !== 1)
        throw new Error('This tile supports one active display or one mirrored display group. Use Displays settings for extended desktops.');

    const [x, y, scale, transform, primary, specs] = logicalMonitors[0];
    const displays = specs.map(spec => {
        const monitor = monitors.find(([candidate]) =>
            candidate.every((part, index) => part === spec[index]));
        if (!monitor)
            throw new Error(`Display ${spec[0]} is no longer connected.`);

        const [, modes, monitorProperties] = monitor;
        const mode = modes.find(candidate => candidate[6]['is-current']);
        if (!mode)
            throw new Error(`Cannot determine the current mode of ${spec[0]}.`);
        return {spec, mode, properties: monitorProperties};
    });
    if (!displays.length)
        throw new Error('No active display is available.');

    const scales = displays[0].mode[5]
        .filter(candidate => candidate >= 1 && displays.every(display =>
            display.mode[5].some(value => sameScale(candidate, value))))
        .sort((left, right) => left - right);

    const leasedDisplays = monitors
        .filter(([, , options]) => options['is-for-lease'])
        .map(([spec]) => spec);
    return {serial, displays, properties, leasedDisplays, x, y, scale, transform, primary, scales};
}

export function buildConfiguration(state, requestedScale) {
    const scale = state.scales.find(candidate => sameScale(candidate, requestedScale));
    if (scale === undefined)
        throw new Error(`${percent(requestedScale)} is not supported by the current display mode.`);

    const monitors = state.displays.map(({spec, mode, properties}) => {
        const options = {};
        if ('is-underscanning' in properties)
            options.underscanning = properties['is-underscanning'];
        if ('color-mode' in properties)
            options['color-mode'] = properties['color-mode'];
        return [spec[0], mode[0], options];
    });
    const properties = {};
    if (state.properties['supports-changing-layout-mode'] &&
        'layout-mode' in state.properties)
        properties['layout-mode'] = state.properties['layout-mode'];

    if (state.leasedDisplays.length)
        properties['monitors-for-lease'] = state.leasedDisplays;

    return {
        serial: state.serial,
        logicalMonitors: [[
            state.x, state.y, scale, state.transform, state.primary, monitors,
        ]],
        properties,
    };
}
