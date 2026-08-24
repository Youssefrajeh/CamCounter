// Interactive Canvas Editor for Tripwires and Zones on Live Video

class CanvasEditor {
  constructor(canvasId, containerId) {
    this.canvas = document.getElementById(canvasId);
    this.container = document.getElementById(containerId);
    this.ctx = this.canvas.getContext('2d');
    
    this.mode = 'idle'; // 'idle', 'draw_line', 'draw_zone'
    this.points = [];
    this.onShapeCreated = null;

    this._setupEvents();
    this._resizeCanvas();
    window.addEventListener('resize', () => this._resizeCanvas());
  }

  _resizeCanvas() {
    if (!this.canvas || !this.container) return;
    const rect = this.container.getBoundingClientRect();
    this.canvas.width = rect.width;
    this.canvas.height = rect.height;
    this.render();
  }

  _setupEvents() {
    this.canvas.addEventListener('click', (e) => this._handleClick(e));
    this.canvas.addEventListener('mousemove', (e) => this._handleMouseMove(e));
  }

  startDrawLine(callback) {
    this.mode = 'draw_line';
    this.points = [];
    this.onShapeCreated = callback;
    this.canvas.classList.add('interactive');
    this._updateInstructions('Click 2 points on the camera to set the tripwire line.');
  }

  startDrawZone(callback) {
    this.mode = 'draw_zone';
    this.points = [];
    this.onShapeCreated = callback;
    this.canvas.classList.add('interactive');
    this._updateInstructions('Click 3 or 4 points to define polygon zone, then double-click to finish.');
  }

  cancelDrawing() {
    this.mode = 'idle';
    this.points = [];
    this.canvas.classList.remove('interactive');
    this._updateInstructions('');
    this.render();
  }

  _updateInstructions(text) {
    const el = document.getElementById('drawing-instructions');
    const cancelBtn = document.getElementById('btn-clear-drawings');
    if (el) {
      el.textContent = text;
      el.style.display = text ? 'inline' : 'none';
    }
    if (cancelBtn) {
      cancelBtn.style.display = text ? 'inline-flex' : 'none';
    }
  }

  _handleClick(e) {
    if (this.mode === 'idle') return;

    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const normX = Math.max(0, Math.min(1, x / this.canvas.width));
    const normY = Math.max(0, Math.min(1, y / this.canvas.height));

    this.points.push({ x: normX, y: normY, px: x, py: y });

    if (this.mode === 'draw_line' && this.points.length === 2) {
      const lineData = {
        id: 'line_' + Date.now().toString(36),
        name: 'Tripwire ' + (Math.floor(Math.random() * 89) + 10),
        p1: { x: this.points[0].x, y: this.points[0].y },
        p2: { x: this.points[1].x, y: this.points[1].y },
        in_label: 'IN',
        out_label: 'OUT',
        color: '#10B981',
        active: true
      };
      if (this.onShapeCreated) this.onShapeCreated('line', lineData);
      this.cancelDrawing();
    } else if (this.mode === 'draw_zone' && this.points.length >= 4) {
      const zoneData = {
        id: 'zone_' + Date.now().toString(36),
        name: 'Zone ' + (Math.floor(Math.random() * 89) + 10),
        points: this.points.map(p => ({ x: p.x, y: p.y })),
        max_capacity: 10,
        color: '#3B82F6',
        active: true
      };
      if (this.onShapeCreated) this.onShapeCreated('zone', zoneData);
      this.cancelDrawing();
    } else {
      this.render();
    }
  }

  _handleMouseMove(e) {
    if (this.mode === 'idle' || this.points.length === 0) return;
    const rect = this.canvas.getBoundingClientRect();
    const currentX = e.clientX - rect.left;
    const currentY = e.clientY - rect.top;

    this.render();

    // Draw preview line to mouse
    this.ctx.strokeStyle = '#06B6D4';
    this.ctx.lineWidth = 2;
    this.ctx.setLineDash([6, 6]);
    this.ctx.beginPath();
    const lastP = this.points[this.points.length - 1];
    this.ctx.moveTo(lastP.x * this.canvas.width, lastP.y * this.canvas.height);
    this.ctx.lineTo(currentX, currentY);
    this.ctx.stroke();
    this.ctx.setLineDash([]);
  }

  render() {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    if (this.points.length > 0) {
      this.ctx.strokeStyle = '#10B981';
      this.ctx.fillStyle = 'rgba(16, 185, 129, 0.2)';
      this.ctx.lineWidth = 2;

      this.ctx.beginPath();
      for (let i = 0; i < this.points.length; i++) {
        const px = this.points[i].x * this.canvas.width;
        const py = this.points[i].y * this.canvas.height;
        if (i === 0) this.ctx.moveTo(px, py);
        else this.ctx.lineTo(px, py);

        // Point circle
        this.ctx.arc(px, py, 4, 0, Math.PI * 2);
      }
      this.ctx.stroke();
      if (this.mode === 'draw_zone' && this.points.length > 2) {
        this.ctx.fill();
      }
    }
  }
}

window.CanvasEditor = CanvasEditor;
