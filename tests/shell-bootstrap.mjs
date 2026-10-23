// This module is loaded only by the disposable headless test compositor.
export function init() {
    global.context.unsafe_mode = true;
}

export async function run() {
    await new Promise(() => {});
}
// SPDX-License-Identifier: GPL-3.0-or-later
