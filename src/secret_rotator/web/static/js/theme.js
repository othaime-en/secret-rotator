/*
 * Theme handling: "system" (default), "light" or "dark".
 *
 * Load this synchronously in <head>, before the stylesheets paint, so the
 * stored choice is applied before first render and the page never flashes
 * the wrong theme. Choosing "system" removes the data-theme attribute and
 * lets the prefers-color-scheme media query in tokens.css decide.
 *
 * Any element with a data-theme-choice="system|light|dark" attribute acts
 * as a theme switch; the one matching the current choice gets
 * aria-pressed="true".
 */
(function () {
    "use strict";

    var KEY = "sr-theme";
    var VALID = ["system", "light", "dark"];
    var root = document.documentElement;

    function read() {
        try {
            var stored = window.localStorage.getItem(KEY);
            return VALID.indexOf(stored) === -1 ? "system" : stored;
        } catch (e) {
            // Storage can be blocked (privacy mode, sandboxed frames).
            return "system";
        }
    }

    function apply(choice) {
        if (choice === "light" || choice === "dark") {
            root.setAttribute("data-theme", choice);
        } else {
            root.removeAttribute("data-theme");
        }
    }

    function syncControls(choice) {
        var controls = document.querySelectorAll("[data-theme-choice]");
        for (var i = 0; i < controls.length; i++) {
            var active = controls[i].getAttribute("data-theme-choice") === choice;
            controls[i].setAttribute("aria-pressed", active ? "true" : "false");
            controls[i].classList.toggle("on", active);
        }
    }

    function set(choice) {
        if (VALID.indexOf(choice) === -1) {
            return;
        }
        try {
            window.localStorage.setItem(KEY, choice);
        } catch (e) {
            // Non-persistent, but still apply it for this page view.
        }
        apply(choice);
        syncControls(choice);
    }

    apply(read());

    document.addEventListener("DOMContentLoaded", function () {
        syncControls(read());
    });

    document.addEventListener("click", function (event) {
        var target = event.target.closest ? event.target.closest("[data-theme-choice]") : null;
        if (target) {
            set(target.getAttribute("data-theme-choice"));
        }
    });

    window.SRTheme = { get: read, set: set };
})();
