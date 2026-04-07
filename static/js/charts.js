document.addEventListener('DOMContentLoaded', function() {
    // Set default colors for the dark theme
    Chart.defaults.color = '#6b7280'; // Light gray for text
    Chart.defaults.borderColor = 'rgba(229, 231, 235, 0.2)'; // Light gray for grid lines

    // --- Blood Pressure Chart Logic ---
    const bpCanvas = document.getElementById('bpChart');
    if (bpCanvas) {
        try {
            const bpData = JSON.parse(bpCanvas.dataset.bp);
            if (bpData && bpData.labels && bpData.labels.length > 0) {
                new Chart(bpCanvas.getContext('2d'), {
                    type: 'line',
                    data: {
                        labels: bpData.labels,
                        datasets: [{
                            label: 'Systolic (mmHg)',
                            data: bpData.systolic,
                            borderColor: '#4f46e5',
                            tension: 0.1
                        }, {
                            label: 'Diastolic (mmHg)',
                            data: bpData.diastolic,
                            borderColor: '#ec4899',
                            tension: 0.1
                        }]
                    }
                });
            }
        } catch (e) {
            console.error("Could not render BP chart:", e);
        }
    }

    // --- Blood Glucose Chart Logic ---
    const glucoseCanvas = document.getElementById('glucoseChart');
    if (glucoseCanvas) {
        try {
            const glucoseData = JSON.parse(glucoseCanvas.dataset.glucose);
            if (glucoseData && glucoseData.labels && glucoseData.labels.length > 0) {
                new Chart(glucoseCanvas.getContext('2d'), {
                    type: 'line',
                    data: {
                        labels: glucoseData.labels,
                        datasets: [{
                            label: 'Glucose (mg/dL)',
                            data: glucoseData.levels,
                            borderColor: '#10b981', // Green color
                            tension: 0.1
                        }]
                    }
                });
            }
        } catch (e) {
            console.error("Could not render Glucose chart:", e);
        }
    }
});