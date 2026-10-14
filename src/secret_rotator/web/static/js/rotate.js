/*
 * Rotation controls, shared by the Overview and Jobs pages.
 *
 * - [data-action="rotate-all"] starts a rotation sweep (POST /api/rotate)
 *   and shows progress in #rotate-banner until it finishes, then reloads
 *   the page so the tables show the new state. A sweep that is already
 *   running (HTTP 409) is joined, not restarted.
 * - [data-action="rotate-job"][data-job="name"] rotates one job
 *   (POST /api/jobs/<name>/rotate). The result is stashed in
 *   sessionStorage and shown in the banner after the page reloads.
 * - An element with data-refresh-ms (set by the server while jobs are
 *   rotating) reloads the page on that interval.
 *
 * Only one rotation can run at a time, so every rotation button is
 * disabled while one is in flight.
 */
(function () {
    "use strict";

    var POLL_MS = 1500;
    var MAX_POLL_ERRORS = 5;
    var FLASH_KEY = "sr-flash";
    var BUSY_SELECTOR =
        '[data-action="rotate-all"], [data-open="m-rotate"], [data-action="rotate-job"]';

    var banner = document.getElementById("rotate-banner");
    var bannerText = document.getElementById("rotate-banner-text");
    var bannerBar = banner ? banner.querySelector(".bar") : null;
    var bannerFill = banner ? banner.querySelector(".bar i") : null;
    var tracking = false;

    function show(message, percent, failed) {
        if (!banner) {
            return;
        }
        banner.hidden = false;
        banner.classList.toggle("is-error", !!failed);
        bannerText.textContent = message;
        if (bannerBar) {
            bannerBar.hidden = percent === null || !!failed;
        }
        if (bannerFill && percent !== null) {
            bannerFill.style.width = percent + "%";
        }
    }

    function setBusy(busy) {
        var buttons = document.querySelectorAll(BUSY_SELECTOR);
        for (var i = 0; i < buttons.length; i++) {
            buttons[i].disabled = busy;
        }
    }

    function sleep(ms) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, ms);
        });
    }

    function requestError(error) {
        if (error.status === 429) {
            return "Too many rotation requests. Wait a minute and try again.";
        }
        if (error.status === 409) {
            return "Another rotation is already running. Try again when it finishes.";
        }
        return error.message;
    }

    // ---- rotate all -------------------------------------------------

    async function track(jobId) {
        if (tracking) {
            return;
        }
        tracking = true;
        setBusy(true);
        show("Starting rotation", 0, false);

        var errors = 0;
        while (true) {
            var job;
            try {
                job = await SR.api.get("/api/rotate/" + encodeURIComponent(jobId));
                errors = 0;
            } catch (error) {
                if (error.status === 404) {
                    window.location.reload();
                    return;
                }
                errors += 1;
                if (errors >= MAX_POLL_ERRORS) {
                    show("Lost contact with the rotation job: " + error.message, null, true);
                    tracking = false;
                    setBusy(false);
                    return;
                }
                await sleep(POLL_MS);
                continue;
            }

            var done = (job.progress && job.progress.completed) || 0;
            var total = (job.progress && job.progress.total) || 0;

            if (job.status === "completed") {
                var results = job.results || {};
                var failed = Object.keys(results).filter(function (name) {
                    return !results[name];
                }).length;
                show(failed ? "Rotation finished, " + failed + " failed" : "Rotation finished", 100, false);
                window.setTimeout(function () {
                    window.location.reload();
                }, 900);
                return;
            }
            if (job.status === "failed") {
                show("Rotation failed: " + (job.error || "unknown error"), null, true);
                tracking = false;
                setBusy(false);
                return;
            }

            show(
                "Rotating, " + done + " of " + total + " done",
                total ? Math.round((done / total) * 100) : 0,
                false
            );
            await sleep(POLL_MS);
        }
    }

    async function rotateAll() {
        if (window.SROverlay) {
            window.SROverlay.close(false);
        }
        setBusy(true);
        try {
            var job = await SR.api.post("/api/rotate");
            await track(job.job_id);
        } catch (error) {
            if (error.status === 409 && error.body && error.body.job_id) {
                await track(error.body.job_id);
                return;
            }
            show("Could not start rotation: " + requestError(error), null, true);
            setBusy(false);
        }
    }

    // ---- rotate one job ---------------------------------------------

    function stashFlash(message, failed) {
        try {
            window.sessionStorage.setItem(FLASH_KEY, JSON.stringify({ message: message, failed: failed }));
        } catch (e) {
            // Storage unavailable: the table still shows the new state.
        }
    }

    function showStashedFlash() {
        var raw = null;
        try {
            raw = window.sessionStorage.getItem(FLASH_KEY);
            window.sessionStorage.removeItem(FLASH_KEY);
        } catch (e) {
            return;
        }
        if (!raw) {
            return;
        }
        try {
            var flash = JSON.parse(raw);
            show(flash.message, null, flash.failed);
        } catch (e) {
            // Ignore a malformed value.
        }
    }

    async function rotateJob(button) {
        var name = button.getAttribute("data-job");
        setBusy(true);
        show("Rotating " + name, null, false);
        try {
            var result = await SR.api.post("/api/jobs/" + encodeURIComponent(name) + "/rotate");
            var reason = result.job && result.job.last_error ? ": " + result.job.last_error : "";
            stashFlash(result.success ? name + " rotated" : name + " failed" + reason, !result.success);
            window.location.reload();
        } catch (error) {
            show("Could not rotate " + name + ": " + requestError(error), null, true);
            setBusy(false);
        }
    }

    document.addEventListener("click", function (event) {
        if (event.target.closest('[data-action="rotate-all"]')) {
            rotateAll();
            return;
        }
        var jobButton = event.target.closest('[data-action="rotate-job"]');
        if (jobButton && !jobButton.disabled) {
            rotateJob(jobButton);
        }
    });

    showStashedFlash();

    var refresh = document.querySelector("[data-refresh-ms]");
    if (refresh) {
        window.setTimeout(function () {
            if (!tracking) {
                window.location.reload();
            }
        }, parseInt(refresh.getAttribute("data-refresh-ms"), 10) || 3000);
    }
})();
