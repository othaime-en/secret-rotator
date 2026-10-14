/*
 * Localises <time datetime="..." data-fmt="..."> elements.
 *
 * The server renders every timestamp in UTC as a fallback; this script
 * rewrites the text in the viewer's own time zone. Formats:
 *   clock     04:00
 *   datetime  Today, 04:00 / Oct 8, 04:00 (year added when not this year)
 *   date      Today / Oct 8
 *   activity  04:00 when it is today, otherwise Oct 8
 */
(function () {
    "use strict";

    var clockFormat = new Intl.DateTimeFormat(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
    });

    function startOfDay(date) {
        return new Date(date.getFullYear(), date.getMonth(), date.getDate());
    }

    function dayLabel(date, now) {
        var days = Math.round((startOfDay(date) - startOfDay(now)) / 86400000);
        if (days === 0) {
            return "Today";
        }
        if (days === 1) {
            return "Tomorrow";
        }
        if (days === -1) {
            return "Yesterday";
        }
        var options = { month: "short", day: "numeric" };
        if (date.getFullYear() !== now.getFullYear()) {
            options.year = "numeric";
        }
        return new Intl.DateTimeFormat(undefined, options).format(date);
    }

    function format(date, fmt, now) {
        now = now || new Date();
        switch (fmt) {
            case "clock":
                return clockFormat.format(date);
            case "date":
                return dayLabel(date, now);
            case "activity":
                return dayLabel(date, now) === "Today" ? clockFormat.format(date) : dayLabel(date, now);
            case "datetime":
            default:
                return dayLabel(date, now) + ", " + clockFormat.format(date);
        }
    }

    function apply(root) {
        var nodes = (root || document).querySelectorAll("time[datetime][data-fmt]");
        var now = new Date();
        for (var i = 0; i < nodes.length; i++) {
            var date = new Date(nodes[i].getAttribute("datetime"));
            if (isNaN(date.getTime())) {
                continue;
            }
            nodes[i].textContent = format(date, nodes[i].getAttribute("data-fmt"), now);
            nodes[i].title = date.toLocaleString();
        }
    }

    window.SRTime = { format: format, apply: apply };
    apply(document);
})();
