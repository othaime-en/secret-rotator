/*
 * One-shot messages that survive a page reload.
 *
 * After an action succeeds (a rotation, a restore) the page reloads so its
 * tables show the new state. The result message is stashed in
 * sessionStorage first and shown by the page's script once it has loaded.
 *
 *   SR.flash.stash("db restored", false)   before reloading
 *   SR.flash.consume()                     -> {message, failed} or null
 */
(function () {
    "use strict";

    var KEY = "sr-flash";

    window.SR = window.SR || {};
    window.SR.flash = {
        stash: function (message, failed) {
            try {
                window.sessionStorage.setItem(KEY, JSON.stringify({ message: message, failed: !!failed }));
            } catch (e) {
                // Storage unavailable: the page still shows the new state.
            }
        },
        consume: function () {
            var raw;
            try {
                raw = window.sessionStorage.getItem(KEY);
                window.sessionStorage.removeItem(KEY);
            } catch (e) {
                return null;
            }
            if (!raw) {
                return null;
            }
            try {
                var flash = JSON.parse(raw);
                return flash && typeof flash.message === "string" ? flash : null;
            } catch (e) {
                return null;
            }
        },
    };
})();
