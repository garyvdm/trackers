function format_time_delta(seconds, show_days) {
    if (show_days) {
        return sprintf('%id %02i:%02i:%02i',
            Math.floor(seconds / 60 / 60 / 24), /* days */
            Math.floor(seconds / 60 / 60 % 24), /* hours */
            Math.floor(seconds / 60 % 60),      /* min */
            Math.floor(seconds % 60)            /* seconds */
            );
    } else {
        return sprintf('%02i:%02i:%02i',
            Math.floor(seconds / 60 / 60),      /* hours */
            Math.floor(seconds / 60 % 60),      /* min */
            Math.floor(seconds % 60)            /* seconds */
            );
    }
}
