document.addEventListener('DOMContentLoaded', function() {
    var calendarEl = document.getElementById('calendar');

    var calendar = new FullCalendar.Calendar(calendarEl, {
        initialView: 'dayGridMonth',
        height: '100%',
        events: window.calendarEvents || []
    });

    calendar.render();
});
document.addEventListener('DOMContentLoaded', function() {
    var calendarEl = document.getElementById('calendar');

    var calendar = new FullCalendar.Calendar(calendarEl, {
        initialView: 'dayGridMonth',
        height: '100%',
        events: window.calendarEvents || [],
        
        dateClick: function(info) {
            window.location.href = "/day/" + info.dateStr;
        }
    });

    calendar.render();
});
