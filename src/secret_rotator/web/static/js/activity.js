/*
 * Activity page: keeps the log pane in sync with new events.
 *
 * While "Live" is on (page 1 only) the page polls
 * GET /api/activity?after=<latest seq> with the page's level and search
 * filters and appends whatever is new. If the viewer has scrolled up to
 * read, the view is left where it is. Polling pauses while the tab is
 * hidden or the viewer pauses it, and stops after repeated failures.
 *
 * Lines are built with textContent, never HTML. lineFor() must stay in
 * step with the log_line macro in templates/partials/macros.html.
 */
(function () {
    "use strict";

    var pane = document.getElementById("log");
    var toggle = document.getElementById("live-toggle");
    if (!pane) {
        return;
    }

    var POLL_MS = parseInt(pane.getAttribute("data-poll-ms"), 10) || 3000;
    var MAX_LINES = 500;
    var MAX_ERRORS = 5;
    var BATCH_LIMIT = 200; // the API's page size for "after" requests
    var ROUTINE = { rotate: 1, restore: 1, verify_backups: 1 };

    var state = {
        after: parseInt(pane.getAttribute("data-latest"), 10) || 0,
        live: pane.getAttribute("data-live") === "true",
        level: pane.getAttribute("data-level") || "all",
        query: pane.getAttribute("data-q") || "",
        errors: 0,
        timer: null,
        busy: false,
    };

    function lineFor(event) {
        var kind;
        if (event.level === "error") {
            kind = ["e", "ERROR"];
        } else if (event.level === "warning") {
            kind = ["w", "WARN"];
        } else if (ROUTINE[event.action]) {
            kind = ["s", "OK"];
        } else {
            kind = ["i", "INFO"];
        }

        var line = document.createElement("div");
        line.className = "line";
        line.setAttribute("data-seq", String(event.seq));

        var when = document.createElement("span");
        when.className = "t";
        var time = document.createElement("time");
        time.setAttribute("datetime", event.timestamp);
        time.setAttribute("data-fmt", "logstamp");
        time.textContent = event.timestamp;
        when.appendChild(time);

        var level = document.createElement("span");
        level.className = "lv " + kind[0];
        level.textContent = kind[1];

        var message = document.createElement("span");
        message.className = "msg";
        message.textContent = event.message;

        line.appendChild(when);
        line.appendChild(document.createTextNode(" "));
        line.appendChild(level);
        line.appendChild(document.createTextNode(" "));
        line.appendChild(message);
        return line;
    }

    function nearBottom() {
        return pane.scrollHeight - pane.scrollTop - pane.clientHeight < 24;
    }

    function scrollToEnd() {
        pane.scrollTop = pane.scrollHeight;
    }

    function append(events) {
        var follow = nearBottom();
        var empty = document.getElementById("log-empty");
        if (empty) {
            empty.remove();
        }

        // The API returns newest first; the pane reads oldest to newest.
        for (var i = events.length - 1; i >= 0; i--) {
            var line = lineFor(events[i]);
            pane.appendChild(line);
            if (window.SRTime) {
                window.SRTime.apply(line);
            }
        }
        while (pane.querySelectorAll(".line").length > MAX_LINES) {
            pane.querySelector(".line").remove();
        }
        if (follow) {
            scrollToEnd();
        }
    }

    function setLive(on, label) {
        state.live = on;
        if (toggle) {
            toggle.setAttribute("aria-pressed", on ? "true" : "false");
            toggle.classList.toggle("acc", on);
            toggle.textContent = label || (on ? "Live" : "Paused");
        }
        window.clearTimeout(state.timer);
        state.timer = null;
        if (on) {
            schedule();
        }
    }

    function schedule(delay) {
        window.clearTimeout(state.timer);
        state.timer = window.setTimeout(poll, delay === undefined ? POLL_MS : delay);
    }

    async function poll() {
        state.timer = null;
        if (!state.live) {
            return;
        }
        if (document.hidden || state.busy) {
            schedule();
            return;
        }

        state.busy = true;
        try {
            var url =
                "/api/activity?after=" +
                state.after +
                "&level=" +
                encodeURIComponent(state.level) +
                "&q=" +
                encodeURIComponent(state.query);
            var data = await SR.api.get(url);
            state.errors = 0;

            // The audit file was rotated, or more happened than one poll
            // returns: a reload is the only way to get a consistent view.
            if (data.latest_seq < state.after || data.events.length >= BATCH_LIMIT) {
                window.location.reload();
                return;
            }
            if (data.events.length) {
                append(data.events);
            }
            state.after = data.latest_seq;
        } catch (error) {
            state.errors += 1;
            if (state.errors >= MAX_ERRORS) {
                setLive(false, "Disconnected");
                return;
            }
        } finally {
            state.busy = false;
        }
        if (state.live) {
            schedule();
        }
    }

    if (toggle) {
        toggle.addEventListener("click", function () {
            state.errors = 0;
            setLive(!state.live || toggle.textContent === "Disconnected");
        });
    }

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden && state.live) {
            schedule(0);
        }
    });

    scrollToEnd();
    if (state.live) {
        schedule();
    }
})();
