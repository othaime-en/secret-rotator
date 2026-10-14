/*
 * Small fetch wrapper for the JSON API.
 *
 * - Sends the CSRF token (from <meta name="csrf-token">) on every request;
 *   Flask-WTF rejects state-changing requests without it.
 * - Resolves to the parsed JSON body; rejects with an Error carrying
 *   .status and .body for any non-2xx response.
 * - Sends the browser to the sign-in page if the session has expired.
 */
(function () {
    "use strict";

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") : "";
    }

    async function request(method, url, body) {
        var options = {
            method: method,
            credentials: "same-origin",
            headers: { Accept: "application/json", "X-CSRFToken": csrfToken() },
        };
        if (body !== undefined) {
            options.headers["Content-Type"] = "application/json";
            options.body = JSON.stringify(body);
        }

        var response = await fetch(url, options);

        if (response.status === 401) {
            window.location.assign("/login?next=" + encodeURIComponent(window.location.pathname));
            throw new Error("Session expired");
        }

        var data = null;
        try {
            data = await response.json();
        } catch (e) {
            // Non-JSON body: fall through to the status check below.
        }

        if (!response.ok) {
            var error = new Error((data && (data.error || data.message)) || "HTTP " + response.status);
            error.status = response.status;
            error.body = data;
            throw error;
        }
        return data;
    }

    window.SR = window.SR || {};
    window.SR.api = {
        get: function (url) {
            return request("GET", url);
        },
        post: function (url, body) {
            return request("POST", url, body);
        },
        put: function (url, body) {
            return request("PUT", url, body);
        },
        del: function (url) {
            return request("DELETE", url);
        },
    };
})();
