/*
 * Settings page.
 *
 * - Sections are tabs (one panel at a time, #section in the URL) once this
 *   script runs; without it every section is shown in turn.
 * - Every edit is tracked against the value the page was rendered with
 *   (data-original). Nothing is sent until Save: the "Unsaved changes" bar
 *   appears with the first edit, Discard puts everything back, and leaving
 *   the page with unsaved edits asks first.
 * - Save sends one PUT /api/settings with the changed values, the keys
 *   staged for reset and the settings revision the page loaded; the server
 *   validates the whole form and saves all of it or none of it. Messages
 *   appear beside the setting they concern.
 * - Credentials are write-only: the field only ever holds what you type to
 *   replace the stored value, and Clear stages removing it.
 */
(function () {
    "use strict";

    var form = document.getElementById("settings-form");
    var root = document.getElementById("settings-root");
    if (!form || !root) {
        return;
    }

    var savebar = document.getElementById("savebar");
    var saveButton = document.getElementById("save");
    var staged = {}; // key -> true, for settings whose dashboard value is to be dropped
    var saving = false;
    var allowUnload = false;

    function $(id) {
        return document.getElementById(id);
    }

    function rows() {
        return form.querySelectorAll("[data-row]");
    }

    function rowFor(key) {
        return form.querySelector('[data-row="' + key + '"]');
    }

    function plainControls() {
        return form.querySelectorAll("[data-key][data-kind]:not(.secret-ctl)");
    }

    function secretBoxes() {
        return form.querySelectorAll(".secret-ctl");
    }

    // ---- sections ---------------------------------------------------------

    var links = root.querySelectorAll("a[data-sec]");

    function select(key, updateHash) {
        var found = false;
        links.forEach(function (link) {
            var on = link.getAttribute("data-sec") === key;
            found = found || on;
            link.classList.toggle("on", on);
            if (on) {
                link.setAttribute("aria-current", "true");
            } else {
                link.removeAttribute("aria-current");
            }
        });
        if (!found) {
            return false;
        }
        root.querySelectorAll(".spanel").forEach(function (panel) {
            panel.classList.toggle("on", panel.id === "s-" + key);
        });
        if (updateHash && window.history && window.history.replaceState) {
            window.history.replaceState(null, "", "#" + key);
        }
        return true;
    }

    root.classList.add("tabs");
    root.addEventListener("click", function (event) {
        var link = event.target.closest("a[data-sec]");
        if (link) {
            event.preventDefault();
            select(link.getAttribute("data-sec"), true);
        }
    });
    window.addEventListener("hashchange", function () {
        select(window.location.hash.replace("#", ""), false);
    });
    if (!select(window.location.hash.replace("#", ""), false) && links.length) {
        select(links[0].getAttribute("data-sec"), false);
    }

    // ---- reading and comparing values ---------------------------------------

    function readValue(el) {
        var kind = el.getAttribute("data-kind");
        if (kind === "bool") {
            return el.checked;
        }
        if (kind === "int") {
            return el.value === "" ? null : Number(el.value);
        }
        return el.value;
    }

    function original(el) {
        return JSON.parse(el.getAttribute("data-original"));
    }

    function changed(el) {
        return readValue(el) !== original(el);
    }

    function secretInput(box) {
        return box.querySelector("[data-secret-input]");
    }

    function secretReplaced(box) {
        var input = secretInput(box);
        return !input.hidden && input.value !== "";
    }

    // ---- tracking changes -----------------------------------------------------

    function isDirty() {
        var dirty = false;
        plainControls().forEach(function (el) {
            if (changed(el)) {
                dirty = true;
            }
        });
        secretBoxes().forEach(function (box) {
            if (secretReplaced(box)) {
                dirty = true;
            }
        });
        return dirty || Object.keys(staged).length > 0;
    }

    function refresh() {
        rows().forEach(function (row) {
            var key = row.getAttribute("data-row");
            var control = row.querySelector("[data-key][data-kind]");
            var rowChanged = !!staged[key];
            if (control && control.classList.contains("secret-ctl")) {
                rowChanged = rowChanged || secretReplaced(control);
            } else if (control) {
                rowChanged = rowChanged || changed(control);
            }
            row.classList.toggle("changed", rowChanged);
            row.classList.toggle("reset-staged", !!staged[key]);

            var badge = row.querySelector(".restart-badge");
            if (badge && row.getAttribute("data-restart") === "true" && !badge.hasAttribute("data-pending")) {
                badge.hidden = !rowChanged;
            }
        });
        savebar.classList.toggle("show", isDirty());
    }

    form.addEventListener("input", refresh);
    form.addEventListener("change", refresh);

    window.addEventListener("beforeunload", function (event) {
        if (isDirty() && !allowUnload) {
            event.preventDefault();
            event.returnValue = "";
        }
    });

    // ---- credentials and resets -------------------------------------------------

    form.addEventListener("click", function (event) {
        var button = event.target.closest("[data-action]");
        if (!button) {
            return;
        }
        var action = button.getAttribute("data-action");
        var row = button.closest("[data-row]");
        var key = row ? row.getAttribute("data-row") : null;

        if (action === "replace-secret") {
            var input = secretInput(row.querySelector(".secret-ctl"));
            input.hidden = !input.hidden;
            if (input.hidden) {
                input.value = "";
            } else {
                input.focus();
            }
            refresh();
        } else if (action === "clear-secret" || action === "reset-setting") {
            if (staged[key]) {
                delete staged[key];
            } else {
                staged[key] = true;
            }
            var undo = !!staged[key];
            if (action === "clear-secret") {
                button.textContent = undo ? "Undo clear" : "Clear";
                var badge = row.querySelector(".secret-ctl .badge");
                badge.textContent = undo ? "Will be cleared" : "Set";
            } else {
                button.textContent = undo ? "Undo reset" : "Reset";
            }
            refresh();
        }
    });

    // ---- errors -----------------------------------------------------------------

    function clearErrors() {
        form.querySelectorAll(".field-error").forEach(function (el) {
            el.hidden = true;
            el.textContent = "";
        });
        form.querySelectorAll("[aria-invalid]").forEach(function (el) {
            el.removeAttribute("aria-invalid");
        });
    }

    function controlFor(key) {
        var row = rowFor(key);
        if (!row) {
            return null;
        }
        return row.querySelector("[data-secret-input]:not([hidden])") || row.querySelector("[data-kind]:not(.secret-ctl)") || row.querySelector("[data-action]");
    }

    function showErrors(fields) {
        var first = null;
        var general = [];
        Object.keys(fields).forEach(function (key) {
            var message = $("err-" + key);
            var control = controlFor(key);
            if (message && control) {
                message.textContent = fields[key];
                message.hidden = false;
                control.setAttribute("aria-invalid", "true");
                first = first || control;
            } else {
                general.push((key === "_" ? "" : key + ": ") + fields[key]);
            }
        });
        if (general.length) {
            window.SR.flash.show(general.join("; "), true);
        }
        if (first) {
            var panel = first.closest(".spanel");
            if (panel) {
                select(panel.id.slice(2), true);
            }
            first.focus();
        }
    }

    function describe(error) {
        if (error.status === 429) {
            return "Too many changes in a short time. Wait a minute and try again.";
        }
        if (error.status === 409) {
            return "The settings were changed elsewhere since this page loaded. Reload the page and try again (your edits here will be lost).";
        }
        return error.message;
    }

    // ---- saving -----------------------------------------------------------------

    function collect() {
        var changes = {};
        var reset = Object.keys(staged);

        plainControls().forEach(function (el) {
            var key = el.getAttribute("data-key");
            if (!staged[key] && changed(el)) {
                changes[key] = readValue(el);
            }
        });
        secretBoxes().forEach(function (box) {
            var key = box.getAttribute("data-key");
            if (!staged[key] && secretReplaced(box)) {
                changes[key] = secretInput(box).value;
            }
        });
        return { changes: changes, reset: reset };
    }

    function labelOf(key) {
        var row = rowFor(key);
        var label = row ? row.querySelector("b") : null;
        return label ? label.textContent : key;
    }

    function plural(count, word) {
        return count + " " + word + (count === 1 ? "" : "s");
    }

    function summary(result) {
        var parts = [];
        if (result.saved.length) {
            parts.push("Saved " + plural(result.saved.length, "setting"));
        }
        if (result.reset.length) {
            parts.push("reset " + plural(result.reset.length, "setting"));
        }
        var text = parts.length ? parts.join(", ") : "No changes to save";
        text = text.charAt(0).toUpperCase() + text.slice(1) + ".";
        if (result.restart_required && result.restart_required.length) {
            text += " Restart the daemon to apply: " + result.restart_required.map(labelOf).join(", ") + ".";
        }
        return text;
    }

    function setSaving(on) {
        saving = on;
        saveButton.disabled = on;
        $("discard").disabled = on;
        saveButton.textContent = on ? "Saving…" : "Save changes";
    }

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        if (saving || !isDirty()) {
            return;
        }
        clearErrors();
        setSaving(true);
        var body = collect();
        body.revision = parseInt(form.getAttribute("data-revision"), 10);

        try {
            var result = await window.SR.api.put("/api/settings", body);
            window.SR.flash.stash(summary(result), false);
            allowUnload = true;
            window.location.reload();
        } catch (error) {
            setSaving(false);
            if (error.status === 400 && error.body && error.body.fields) {
                showErrors(error.body.fields);
            } else {
                window.SR.flash.show(describe(error), true);
            }
        }
    });

    // ---- discarding -------------------------------------------------------------

    $("discard").addEventListener("click", function () {
        plainControls().forEach(function (el) {
            var before = original(el);
            if (el.getAttribute("data-kind") === "bool") {
                el.checked = before === true;
            } else {
                el.value = before === null ? "" : before;
            }
        });
        secretBoxes().forEach(function (box) {
            var input = secretInput(box);
            input.value = "";
            input.hidden = true;
            var badge = box.querySelector(".badge");
            badge.textContent = box.getAttribute("data-set") === "true" ? "Set" : "Not set";
        });
        form.querySelectorAll('[data-action="clear-secret"]').forEach(function (b) {
            b.textContent = "Clear";
        });
        form.querySelectorAll('[data-action="reset-setting"]').forEach(function (b) {
            b.textContent = "Reset";
        });
        staged = {};
        clearErrors();
        refresh();
    });

    // ---- message left by the last save --------------------------------------------

    var flash = window.SR.flash.consume();
    if (flash) {
        window.SR.flash.show(flash.message, flash.failed);
    }
    refresh();
})();
