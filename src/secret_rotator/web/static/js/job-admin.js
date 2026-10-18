/*
 * Job management: the create/edit drawer, pause and resume, delete,
 * reset and restore.
 *
 * Every change goes through the JSON API (POST/PUT/DELETE /api/jobs...),
 * which validates it and saves it in the dashboard's own settings file.
 * The settings revision the page was loaded with is sent back, so if
 * something else changed meanwhile the server answers 409 and nothing is
 * overwritten. After a successful change the result is stashed with
 * SR.flash and the page reloads (or moves on) to show the new state.
 */
(function () {
    "use strict";

    var drawer = document.getElementById("d-job");
    var form = document.getElementById("job-form");

    var FIELDS = ["name", "secret_id", "provider", "rotator", "schedule"];
    var mode = null; // {kind: "create"} or {kind: "edit", original: {...}}
    var saving = false;

    function revision() {
        var value = drawer ? parseInt(drawer.getAttribute("data-revision"), 10) : NaN;
        return isNaN(value) ? undefined : value;
    }

    function input(name) {
        return form.elements[name];
    }

    function jobUrl(name, suffix) {
        return "/api/jobs/" + encodeURIComponent(name) + (suffix || "");
    }

    function describe(error) {
        if (error.status === 429) {
            return "Too many changes in a short time. Wait a minute and try again.";
        }
        if (error.status === 409) {
            return "The settings were changed elsewhere since this page loaded. Reload the page and try again.";
        }
        return error.message;
    }

    function notify(message, failed) {
        if (!(window.SR.flash.show && window.SR.flash.show(message, failed))) {
            window.alert(message);
        }
    }

    // ---- form helpers -----------------------------------------------------

    function clearErrors() {
        FIELDS.forEach(function (name) {
            var message = document.getElementById("err-" + name);
            if (message) {
                message.hidden = true;
                message.textContent = "";
            }
            if (input(name)) {
                input(name).removeAttribute("aria-invalid");
            }
        });
        var general = document.getElementById("job-form-error");
        general.hidden = true;
        general.textContent = "";
        document.getElementById("job-reload").hidden = true;
    }

    function showFormError(text) {
        var general = document.getElementById("job-form-error");
        general.textContent = text;
        general.hidden = false;
    }

    function showFieldErrors(fields) {
        var first = null;
        var extra = [];
        Object.keys(fields).forEach(function (name) {
            var message = document.getElementById("err-" + name);
            if (message && input(name)) {
                message.textContent = fields[name];
                message.hidden = false;
                input(name).setAttribute("aria-invalid", "true");
                first = first || input(name);
            } else {
                extra.push((name === "_" ? "" : name + ": ") + fields[name]);
            }
        });
        if (extra.length) {
            showFormError(extra.join("; "));
        }
        if (first) {
            first.focus();
        }
    }

    function ensureOption(select, value) {
        for (var i = 0; i < select.options.length; i++) {
            if (select.options[i].value === value) {
                return;
            }
        }
        var option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
    }

    function setSaving(on) {
        saving = on;
        var save = document.getElementById("job-save");
        save.disabled = on;
        save.textContent = on ? "Saving…" : mode && mode.kind === "edit" ? "Save changes" : "Save job";
    }

    // ---- opening the drawer -----------------------------------------------

    function openDrawer(trigger, title, focusName) {
        document.getElementById("d-job-title").textContent = title;
        window.SROverlay.open("d-job", trigger);
        input(focusName).focus();
    }

    function openCreate(trigger) {
        mode = { kind: "create" };
        clearErrors();
        form.reset();
        input("name").readOnly = false;
        setSaving(false);
        openDrawer(trigger, "New job", "name");
    }

    function openEdit(trigger) {
        var data = document.getElementById("job-data");
        if (!data) {
            return;
        }
        var job = JSON.parse(data.textContent);
        mode = { kind: "edit", original: job };
        clearErrors();
        form.reset();

        input("name").value = job.name;
        input("name").readOnly = true; // a job cannot be renamed
        input("secret_id").value = job.secret_id || "";
        ensureOption(input("provider"), job.provider);
        input("provider").value = job.provider;
        ensureOption(input("rotator"), job.rotator);
        input("rotator").value = job.rotator;
        if (job.schedule) {
            ensureOption(input("schedule"), job.schedule);
        }
        input("schedule").value = job.schedule || "";
        input("notification").checked = !!job.notification;

        setSaving(false);
        openDrawer(trigger, "Edit job", "secret_id");
    }

    // ---- saving -----------------------------------------------------------

    function payload() {
        var body = {
            name: input("name").value.trim(),
            provider: input("provider").value,
            rotator: input("rotator").value,
            secret_id: input("secret_id").value,
        };
        if (input("schedule").value) {
            body.schedule = input("schedule").value; // omitted = follow the default
        }
        var original = mode.kind === "edit" ? mode.original.notification : null;
        if (input("notification").checked) {
            body.notification = true;
        } else if (original !== null && original !== undefined) {
            body.notification = false; // it was set explicitly; keep it explicit
        }
        var current = revision();
        if (current !== undefined) {
            body.revision = current;
        }
        return body;
    }

    async function save(event) {
        event.preventDefault();
        if (saving || !mode) {
            return;
        }
        clearErrors();
        setSaving(true);
        var body = payload();

        try {
            if (mode.kind === "create") {
                await window.SR.api.post("/api/jobs", body);
                window.SR.flash.stash("Created job " + body.name, false);
                window.location.assign("/jobs/" + encodeURIComponent(body.name));
            } else {
                var result = await window.SR.api.put(jobUrl(body.name), body);
                var changed = (result.changed || []).length > 0;
                window.SR.flash.stash(changed ? "Saved changes to " + body.name : "No changes to save", false);
                window.location.reload();
            }
        } catch (error) {
            setSaving(false);
            if (error.status === 400 && error.body && error.body.fields) {
                showFieldErrors(error.body.fields);
            } else if (error.status === 409 && error.body && error.body.fields) {
                showFieldErrors(error.body.fields);
            } else {
                showFormError(describe(error));
                if (error.status === 409) {
                    document.getElementById("job-reload").hidden = false;
                }
            }
        }
    }

    // ---- other actions ----------------------------------------------------

    async function act(button, request, success, done) {
        var buttons = document.querySelectorAll("[data-action]");
        buttons.forEach(function (b) {
            b.disabled = true;
        });
        try {
            var result = await request();
            window.SR.flash.stash(success(result), false);
            done(result);
        } catch (error) {
            buttons.forEach(function (b) {
                b.disabled = false;
            });
            notify(describe(error), true);
        }
    }

    function jobOf(button) {
        return button.getAttribute("data-job");
    }

    function reload() {
        window.location.reload();
    }

    document.addEventListener("click", function (event) {
        var button = event.target.closest("[data-action]");
        if (!button || button.disabled) {
            return;
        }
        var action = button.getAttribute("data-action");
        var name = jobOf(button);

        switch (action) {
            case "new-job":
                openCreate(button);
                break;
            case "edit-job":
                openEdit(button);
                break;
            case "pause-job":
            case "resume-job":
                act(
                    button,
                    function () {
                        return window.SR.api.post(jobUrl(name, action === "pause-job" ? "/pause" : "/resume"), {
                            revision: revision(),
                        });
                    },
                    function () {
                        return (action === "pause-job" ? "Paused job " : "Resumed job ") + name;
                    },
                    reload
                );
                break;
            case "confirm-delete":
                window.SROverlay.close(false);
                act(
                    button,
                    function () {
                        return window.SR.api.del(jobUrl(name));
                    },
                    function () {
                        return "Deleted job " + name;
                    },
                    function () {
                        window.location.assign("/jobs");
                    }
                );
                break;
            case "confirm-reset":
            case "restore-job":
                if (action === "confirm-reset") {
                    window.SROverlay.close(false);
                }
                act(
                    button,
                    function () {
                        return window.SR.api.post(jobUrl(name, "/reset"), { revision: revision() });
                    },
                    function (result) {
                        if (action === "restore-job") {
                            return "Restored job " + name;
                        }
                        return result.job ? "Reset job " + name + " to config.yaml" : "Removed job " + name;
                    },
                    function (result) {
                        if (action === "confirm-reset" && !result.job) {
                            window.location.assign("/jobs");
                        } else {
                            reload();
                        }
                    }
                );
                break;
            default:
                break;
        }
    });

    if (form) {
        form.addEventListener("submit", save);
        document.getElementById("job-reload").addEventListener("click", reload);
    }
})();
