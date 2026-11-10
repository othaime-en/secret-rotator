/*
 * Client-side table search.
 *
 *   <input type="search" data-filter-rows="#rows" data-filter-empty="#none">
 *   <tbody id="rows"><tr data-search="name secret_id ...">...</tr></tbody>
 *   <p id="none" hidden>No matches</p>
 *
 * Rows whose data-search text does not contain the query (case-insensitive,
 * every word must match) get the hidden attribute; the empty-state element
 * is shown when nothing matches.
 */
(function () {
    "use strict";

    function apply(input) {
        var body = document.querySelector(input.getAttribute("data-filter-rows"));
        if (!body) {
            return;
        }
        var words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
        var rows = body.querySelectorAll("tr[data-search]");
        var visible = 0;

        for (var i = 0; i < rows.length; i++) {
            var haystack = rows[i].getAttribute("data-search").toLowerCase();
            var match = words.every(function (word) {
                return haystack.indexOf(word) !== -1;
            });
            rows[i].hidden = !match;
            if (match) {
                visible += 1;
            }
        }

        var emptySelector = input.getAttribute("data-filter-empty");
        var empty = emptySelector ? document.querySelector(emptySelector) : null;
        if (empty) {
            empty.hidden = visible !== 0 || rows.length === 0;
        }
    }

    document.addEventListener("input", function (event) {
        if (event.target.matches && event.target.matches("[data-filter-rows]")) {
            apply(event.target);
        }
    });
})();
