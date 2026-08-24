// Charts module for CamCounter AI Dashboard

let liveOccupancyChart = null;
let hourlyFlowChart = null;

const MAX_LIVE_POINTS = 30;
const liveLabels = [];
const liveData = [];

function initCharts() {
  // 1. Live Occupancy Area Chart
  const ctxOcc = document.getElementById('chart-live-occupancy');
  if (ctxOcc) {
    // Fill initial 0s
    for (let i = 0; i < MAX_LIVE_POINTS; i++) {
      liveLabels.push('');
      liveData.push(0);
    }

    liveOccupancyChart = new Chart(ctxOcc, {
      type: 'line',
      data: {
        labels: liveLabels,
        datasets: [{
          label: 'Live Headcount',
          data: liveData,
          borderColor: '#10b981',
          backgroundColor: 'rgba(16, 185, 129, 0.15)',
          borderWidth: 2.5,
          tension: 0.4,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        scales: {
          x: {
            display: false,
            grid: { display: false }
          },
          y: {
            beginAtZero: true,
            ticks: {
              stepSize: 1,
              color: '#64748b',
              font: { family: 'Inter', size: 11 }
            },
            grid: { color: 'rgba(255, 255, 255, 0.05)' }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#101522',
            titleColor: '#94a3b8',
            bodyColor: '#10b981',
            borderColor: 'rgba(255,255,255,0.1)',
            borderWidth: 1
          }
        }
      }
    });
  }

  // 2. Hourly Flow Bar Chart
  const ctxFlow = document.getElementById('chart-hourly-flow');
  if (ctxFlow) {
    hourlyFlowChart = new Chart(ctxFlow, {
      type: 'bar',
      data: {
        labels: ['-5h', '-4h', '-3h', '-2h', '-1h', 'Now'],
        datasets: [
          {
            label: 'Total IN',
            data: [0, 0, 0, 0, 0, 0],
            backgroundColor: '#10b981',
            borderRadius: 4
          },
          {
            label: 'Total OUT',
            data: [0, 0, 0, 0, 0, 0],
            backgroundColor: '#f59e0b',
            borderRadius: 4
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            ticks: { color: '#64748b', font: { family: 'Inter', size: 11 } },
            grid: { display: false }
          },
          y: {
            beginAtZero: true,
            ticks: { stepSize: 1, color: '#64748b', font: { family: 'Inter', size: 11 } },
            grid: { color: 'rgba(255, 255, 255, 0.05)' }
          }
        },
        plugins: {
          legend: {
            labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 } }
          }
        }
      }
    });
  }
}

function updateLiveOccupancyChart(count) {
  if (!liveOccupancyChart) return;
  const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  
  liveLabels.shift();
  liveLabels.push(nowStr);
  
  liveData.shift();
  liveData.push(count);

  liveOccupancyChart.update('none');
}

function updateHourlyChart(inCount, outCount) {
  if (!hourlyFlowChart) return;
  const currDataIn = hourlyFlowChart.data.datasets[0].data;
  const currDataOut = hourlyFlowChart.data.datasets[1].data;
  
  currDataIn[currDataIn.length - 1] = inCount;
  currDataOut[currDataOut.length - 1] = outCount;
  
  hourlyFlowChart.update();
}

window.ChartsManager = {
  initCharts,
  updateLiveOccupancyChart,
  updateHourlyChart
};
