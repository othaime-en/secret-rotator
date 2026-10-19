/*
 * Providers and Rotators pages: the add/edit drawer, tests and samples,
 * delete, reset and restore.
 *
 * The type-specific form fields are built from the type descriptions the
 * server embeds in #component-data, which are the same descriptions it
 * validates requests with, so the form cannot offer anything the server
 * will refuse for being unknown. Every change goes through the JSON API,
 * is saved in the dashboard's own settings file, and is applied to the
 * running daemon at once. The settings revision the page loaded with is
 * sent back so a change made elsewhere is reported instead of overwritten.
 */
(function () {
    "use strict";

    var dataNode = document.getElementById("component-data");
    var drawer = document.getElementById("d-component");
    var form = document.getElementById("component-form");
    if (!dataNode || !drawer || !form) {
        return;
    }

    var data = JSON.parse(dataNode.textContent);
    var kind = data.kind; // "providers" or "rotators"
    var noun = data.noun; // "provider" or "rotator"
    var mode = null; // {kind: "create"} or {kind: "edit", item: {...}}
    var saving = false;
    var pendingConfirm = null;

    var STATIC_ERRORS = ["name", "type"];

    function $(id) {
        return document.getElementById(id);
    }

    function itemUrl(name, suffix) {
        return "/api/" + kind + "/" + encodeURIComponent(name) + (suffix || "");
    }

    function typeSpec(key) {
        return data.types.filter(function (t) {
            return t.type === key;
        })[0];
    }

    function itemNamed(name) {
        return data.items.filter(function (i) {
            return i.name === name;
        })[0];
    }

    function describe(error) {
        if (error.status === 429) {
            return "Too many requests in a short time. Wait a minute and try again.";
        }
        if (error.status === 409 && error.body && error.body.conflict) {
            return "The settings were changed elsewhere since this page loaded. Reload the page and try again.";
        }
        return error.message;
    }

    function notify(message, failed) {
        if (!(window.SR.flash.show && window.SR.flash.show(message, failed))) {
            window.alert(message);
        }
    }

    // ---- building the form ------------------------------------------------

    function fieldInput(name) {
        return form.querySelector('[data-field="' + name + '"]');
    }

    function buildField(field, value) {
        var wrap = document.createElement("div");
        var id = "cf-" + field.name;
        var inputEl;

        if (field.kind === "bool") {
            wrap.className = "set";
            var text = document.createElement("div");
            var label = document.createElement("b");
            label.id = id + "-label";
            label.textContent = field.label;
            text.appendChild(label);
            if (field.help) {
                var small = document.createElement("small");
                small.textContent = field.help;
                text.appendChild(small);
            }
            var ctl = document.createElement("div");
            ctl.className = "ctl";
            var sw = document.createElement("label");
            sw.className = "sw";
            inputEl = document.createElement("input");
            inputEl.type = "checkbox";
            inputEl.id = id;
            inputEl.checked = value === undefined || value === null ? !!field["default"] : !!value;
            inputEl.setAttribute("aria-labelledby", label.id);
            sw.appendChild(inputEl);
            sw.appendChild(document.createElement("i"));
            ctl.appendChild(sw);
            wrap.appendChild(text);
            wrap.appendChild(ctl);
        } else {
            wrap.className = "field";
            var caption = document.createElement("label");
            caption.setAttribute("for", id);
            caption.textContent = field.label;
            wrap.appendChild(caption);

            if (field.kind === "choice") {
                inputEl = document.createElement("select");
                field.choices.forEach(function (choice) {
                    var option = document.createElement("option");
                    option.value = choice;
                    option.textContent = choice;
                    inputEl.appendChild(option);
                });
                inputEl.value = value === undefined || value === null ? field["default"] : value;
            } else {
                inputEl = document.createElement("input");
                if (field.kind === "int") {
                    inputEl.type = "number";
                    inputEl.step = "1";
                    inputEl.min = field.min;
                    inputEl.max = field.max;
                } else {
                    inputEl.type = "text";
                    inputEl.autocomplete = "off";
                    if (field.max) {
                        inputEl.maxLength = field.max;
                    }
                }
                var shown = value === undefined || value === null ? field["default"] : value;
                inputEl.value = shown === null || shown === undefined ? "" : shown;
                if (field.kind === "path") {
                    inputEl.classList.add("mono");
                }
            }
            inputEl.id = id;
            inputEl.className = (inputEl.className + " input").trim();
            wrap.appendChild(inputEl);
            if (field.help) {
                var help = document.createElement("span");
                help.className = "help";
                help.textContent = field.help;
                wrap.appendChild(help);
            }
        }

        inputEl.setAttribute("data-field", field.name);
        inputEl.setAttribute("data-kind", field.kind);
        inputEl.setAttribute("aria-describedby", "err-" + field.name);
        var error = document.createElement("small");
        error.className = "field-error";
        error.id = "err-" + field.name;
        error.hidden = true;
        wrap.appendChild(error);
        return wrap;
    }

    function renderFields(typeKey, values) {
        var container = $("component-fields");
        container.textContent = "";
        var spec = typeSpec(typeKey);
        if (!spec) {
            return;
        }
        spec.fields.forEach(function (field) {
            container.appendChild(buildField(field, values ? values[field.name] : undefined));
        });
    }

    // ---- errors -----------------------------------------------------------

    function clearErrors() {
        form.querySelectorAll(".field-error").forEach(function (el) {
            el.hidden = true;
            el.textContent = "";
        });
        form.querySelectorAll("[aria-invalid]").forEach(function (el) {
            el.removeAttribute("aria-invalid");
        });
        var general = $("component-error");
        general.hidden = true;
        general.textContent = "";
        $("component-reload").hidden = true;
    }

    function showGeneral(text) {
        var general = $("component-error");
        general.textContent = text;
        general.hidden = false;
    }

    function inputFor(name) {
        return STATIC_ERRORS.indexOf(name) !== -1 ? form.elements[name] : fieldInput(name);
    }

    function showFieldErrors(fields) {
        var first = null;
        var extra = [];
        Object.keys(fields).forEach(function (name) {
            var message = $("err-" + name);
            var input = inputFor(name);
            if (message && input) {
                message.textContent = fields[name];
                message.hidden = false;
                input.setAttribute("aria-invalid", "true");
                first = first || input;
            } else {
                extra.push((name === "_" ? "" : name + ": ") + fields[name]);
            }
        });
        if (extra.length) {
            showGeneral(extra.join("; "));
        }
        if (first) {
            first.focus();
        }
    }

    function setSaving(on) {
        saving = on;
        var save = $("component-save");
        save.disabled = on || (mode && mode.item && !mode.item.supported);
        save.textContent = on ? "Saving…" : mode && mode.kind === "edit" ? "Save changes" : "Save " + noun;
    }

    // ---- opening ----------------------------------------------------------

    function populateTypes(selected, locked) {
        var select = $("component-type");
        select.textContent = "";
        data.types.forEach(function (spec) {
            var option = document.createElement("option");
            option.value = spec.type;
            option.textContent = spec.label;
            select.appendChild(option);
        });
        if (selected && !typeSpec(selected)) {
            var unknown = document.createElement("option");
            unknown.value = selected;
            unknown.textContent = selected;
            select.appendChild(unknown);
        }
        select.value = selected || data.types[0].type;
        select.disabled = !!locked;
    }

    function resetSample() {
        var value = $("sample-value");
        if (value) {
            value.textContent = "—";
        }
    }

    function open(trigger, title, focusId) {
        $("d-component-title").textContent = title;
        window.SROverlay.open("d-component", trigger);
        $(focusId).focus();
    }

    function openCreate(trigger) {
        mode = { kind: "create", item: null };
        clearErrors();
        $("component-name").value = "";
        $("component-name").readOnly = false;
        populateTypes(null, false);
        renderFields($("component-type").value, null);
        $("component-unsupported").hidden = true;
        $("component-delete").hidden = true;
        $("component-reset-row").hidden = true;
        resetSample();
        setSaving(false);
        open(trigger, "Add " + noun, "component-name");
    }

    function openEdit(trigger, name) {
        var item = itemNamed(name);
        if (!item) {
            return;
        }
        mode = { kind: "edit", item: item };
        clearErrors();
        $("component-name").value = item.name;
        $("component-name").readOnly = true; // a name cannot change
        populateTypes(item.type, true); // neither can a type
        renderFields(item.type, item.fields);
        $("component-unsupported").hidden = item.supported;

        var del = $("component-delete");
        del.hidden = false;
        del.disabled = item.used_by.length > 0;
        del.title = item.used_by.length ? "Used by: " + item.used_by.join(", ") + ". Change those jobs first." : "";
        $("component-reset-row").hidden = item.source !== "modified";
        resetSample();
        setSaving(false);
        open(trigger, "Edit " + noun, item.supported ? "component-type" : "component-delete");
        if (item.supported) {
            var firstField = form.querySelector("[data-field]");
            if (firstField) {
                firstField.focus();
            }
        }
    }

    // ---- saving -----------------------------------------------------------

    function fieldValues() {
        var values = {};
        form.querySelectorAll("[data-field]").forEach(function (input) {
            var name = input.getAttribute("data-field");
            var fieldKind = input.getAttribute("data-kind");
            if (fieldKind === "bool") {
                values[name] = input.checked;
            } else if (fieldKind === "int") {
                if (input.value !== "") {
                    values[name] = Number(input.value);
                }
            } else if (input.value !== "") {
                values[name] = input.value; // empty = use the default
            }
        });
        return values;
    }

    function payload() {
        var body = { name: $("component-name").value.trim(), type: $("component-type").value };
        var values = fieldValues();
        Object.keys(values).forEach(function (key) {
            body[key] = values[key];
        });
        return body;
    }

    async function save(event) {
        event.preventDefault();
        if (saving || !mode || (mode.item && !mode.item.supported)) {
            return;
        }
        clearErrors();
        setSaving(true);
        var body = payload();
        body.revision = data.revision;

        try {
            if (mode.kind === "create") {
                await window.SR.api.post("/api/" + kind, body);
                window.SR.flash.stash("Added " + noun + " " + body.name, false);
            } else {
                var result = await window.SR.api.put(itemUrl(body.name), body);
                var changed = (result.changed || []).length > 0;
                window.SR.flash.stash(changed ? "Saved changes to " + body.name : "No changes to save", false);
            }
            window.location.reload();
        } catch (error) {
            setSaving(false);
            if ((error.status === 400 || error.status === 409) && error.body && error.body.fields) {
                showFieldErrors(error.body.fields);
            } else {
                showGeneral(describe(error));
                if (error.status === 409) {
                    $("component-reload").hidden = false;
                }
            }
        }
    }

    // ---- sample preview (rotators, in the form) ---------------------------

    async function previewSample() {
        var button = $("sample-button");
        var value = $("sample-value");
        clearErrors();
        button.disabled = true;
        try {
            var result = await window.SR.api.post("/api/rotators/sample", payload());
            value.textContent = result.sample === null ? result.message || "No example for this type." : result.sample;
        } catch (error) {
            value.textContent = "—";
            if (error.status === 400 && error.body && error.body.fields) {
                showFieldErrors(error.body.fields);
            } else {
                showGeneral(describe(error));
            }
        } finally {
            button.disabled = false;
        }
    }

    // ---- row actions ------------------------------------------------------

    function rowOf(button) {
        return button.closest(".reg");
    }

    async function testComponent(button) {
        var name = button.getAttribute("data-name");
        var row = rowOf(button);
        button.disabled = true;
        try {
            if (kind === "providers") {
                var result = await window.SR.api.post(itemUrl(name, "/test"));
                var out = row.querySelector("[data-test-result]");
                out.textContent = result.message;
                out.classList.toggle("bad", !result.ok);
            } else {
                var sample = await window.SR.api.post(itemUrl(name, "/sample"));
                var cell = row.querySelector("[data-sample]");
                cell.textContent = sample.sample === null ? "—" : sample.sample;
            }
        } catch (error) {
            notify(describe(error), true);
        } finally {
            button.disabled = false;
        }
    }

    // ---- confirmations ----------------------------------------------------

    function confirm(title, text, label, action) {
        $("m-component-confirm-title").textContent = title;
        $("m-component-confirm-text").textContent = text;
        $("m-component-confirm-ok").textContent = label;
        pendingConfirm = action;
        window.SROverlay.open("m-component-confirm", null);
    }

    async function perform(request, success, done) {
        try {
            var result = await request();
            window.SR.flash.stash(success(result), false);
            done(result);
        } catch (error) {
            var message = describe(error);
            if (error.status === 409 && error.body && error.body.jobs) {
                message = error.message;
            }
            notify(message, true);
        }
    }

    function revisionBody() {
        return { revision: data.revision };
    }

    function reload() {
        window.location.reload();
    }

    function askDelete() {
        var item = mode && mode.item;
        if (!item) {
            return;
        }
        var text =
            item.source === "dashboard"
                ? item.name + " will be removed."
                : item.name +
                  " is defined in config.yaml, which is not changed: it is only hidden, and you can restore it from this page.";
        confirm("Delete " + noun + "?", text, "Delete", function () {
            perform(
                function () {
                    return window.SR.api.del(itemUrl(item.name));
                },
                function () {
                    return "Deleted " + noun + " " + item.name;
                },
                reload
            );
        });
    }

    function askReset() {
        var item = mode && mode.item;
        if (!item) {
            return;
        }
        confirm(
            "Reset to config.yaml?",
            "The changes made to " + item.name + " in the dashboard are dropped and it goes back to exactly what config.yaml says.",
            "Reset",
            function () {
                perform(
                    function () {
                        return window.SR.api.post(itemUrl(item.name, "/reset"), revisionBody());
                    },
                    function () {
                        return "Reset " + noun + " " + item.name + " to config.yaml";
                    },
                    reload
                );
            }
        );
    }

    document.addEventListener("click", function (event) {
        var button = event.target.closest("[data-action], #sample-button, #m-component-confirm-ok");
        if (!button || button.disabled) {
            return;
        }
        if (button.id === "sample-button") {
            previewSample();
            return;
        }
        if (button.id === "m-component-confirm-ok") {
            var action = pendingConfirm;
            pendingConfirm = null;
            window.SROverlay.close(false);
            if (action) {
                action();
            }
            return;
        }

        var name = button.getAttribute("data-name");
        switch (button.getAttribute("data-action")) {
            case "new-component":
                openCreate(button);
                break;
            case "edit-component":
                openEdit(button, name);
                break;
            case "test-component":
                testComponent(button);
                break;
            case "delete-component":
                askDelete();
                break;
            case "reset-component":
                askReset();
                break;
            case "restore-component":
                perform(
                    function () {
                        return window.SR.api.post(itemUrl(name, "/reset"), revisionBody());
                    },
                    function () {
                        return "Restored " + noun + " " + name;
                    },
                    reload
                );
                break;
            default:
                break;
        }
    });

    form.addEventListener("submit", save);
    $("component-type").addEventListener("change", function () {
        if (mode && mode.kind === "create") {
            renderFields($("component-type").value, null);
            resetSample();
        }
    });
    $("component-reload").addEventListener("click", reload);

    var flash = window.SR.flash.consume();
    if (flash) {
        var notice = $("notice");
        if (notice) {
            notice.hidden = false;
            notice.classList.toggle("is-error", !!flash.failed);
            $("notice-text").textContent = flash.message;
        }
    }
})();
