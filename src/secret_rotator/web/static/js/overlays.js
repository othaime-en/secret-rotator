/*
 * Modal dialogs (and, later, drawers).
 *
 *   <button data-open="m-rotate">   opens the element with that id
 *   <button data-close>             closes whatever is open
 *
 * Overlays are elements with class "modal" or "drawer" and role="dialog".
 * Opening one moves focus into it and keeps Tab inside it; Escape or a click
 * on the scrim closes it and focus returns to the control that opened it.
 */
(function () {
    "use strict";

    var FOCUSABLE =
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    var opener = null;
    var active = null;

    function scrim() {
        return document.getElementById("scrim");
    }

    function close(restoreFocus) {
        var open = document.querySelectorAll(".modal.open, .drawer.open");
        for (var i = 0; i < open.length; i++) {
            open[i].classList.remove("open");
            open[i].setAttribute("aria-hidden", "true");
        }
        if (scrim()) {
            scrim().classList.remove("open");
        }
        if (restoreFocus !== false && opener && opener.focus) {
            opener.focus();
        }
        active = null;
    }

    function open(id, trigger) {
        var element = document.getElementById(id);
        if (!element) {
            return;
        }
        close(false);
        opener = trigger || document.activeElement;
        active = element;
        element.classList.add("open");
        element.setAttribute("aria-hidden", "false");
        if (scrim()) {
            scrim().classList.add("open");
        }
        var target = element.querySelector("[data-autofocus]") || element.querySelector(FOCUSABLE) || element;
        target.focus();
    }

    document.addEventListener("click", function (event) {
        var opening = event.target.closest("[data-open]");
        if (opening) {
            open(opening.getAttribute("data-open"), opening);
        } else if (event.target.closest("[data-close]") || event.target.id === "scrim") {
            close();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (!active) {
            return;
        }
        if (event.key === "Escape") {
            close();
            return;
        }
        if (event.key !== "Tab") {
            return;
        }
        var items = active.querySelectorAll(FOCUSABLE);
        if (!items.length) {
            event.preventDefault();
            return;
        }
        var first = items[0];
        var last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    window.SROverlay = { open: open, close: close };
})();
