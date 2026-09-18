const ALL = "*";
const DATA_ROOT = "data";
const elements = {
  chart: document.querySelector("#chart"),
  status: document.querySelector("#chart-status"),
  host: document.querySelector("#runner-host"),
  label: document.querySelector("#runner-label"),
  repository: document.querySelector("#repository"),
  range: document.querySelector("#time-range"),
  exclusions: document.querySelector("#exclusions"),
  updatedAt: document.querySelector("#updated-at"),
};

let manifest;
let selectedRows = [];
let refreshVersion = 0;
const partitionCache = new Map();

function addOptions(select, values, allLabel) {
  const previous = select.value;
  select.replaceChildren(new Option(allLabel, ALL));
  for (const value of values) {
    select.add(new Option(value, value));
  }
  select.value = values.includes(previous) || previous === ALL ? previous : ALL;
}

function updateDependentFilters() {
  const host = elements.host.value;
  addOptions(elements.label, manifest.labels[host] || [], "All runners");
  addOptions(
    elements.repository,
    manifest.repositories[host] || [],
    "All repositories",
  );
}

function datesForRange() {
  if (elements.range.value === "all") {
    return manifest.dates;
  }
  const days = Number(elements.range.value);
  return manifest.dates.slice(-(days + 1));
}

async function loadPartition(date) {
  if (!partitionCache.has(date)) {
    const request = fetch(`${DATA_ROOT}/date=${date}.json`)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Unable to load report data for ${date}`);
        }
        return response.json();
      })
      .catch((error) => {
        partitionCache.delete(date);
        throw error;
      });
    partitionCache.set(date, request);
  }
  return partitionCache.get(date);
}

async function refreshData() {
  const version = ++refreshVersion;
  elements.status.textContent = "Loading report data...";
  try {
    const partitions = await Promise.all(datesForRange().map(loadPartition));
    if (version !== refreshVersion) {
      return;
    }
    const host = elements.host.value;
    const repository = elements.repository.value;
    const label = elements.label.value;
    let rows = partitions
      .flatMap((partition) => partition.series)
      .filter(
        (row) =>
          row.host === host &&
          row.repository === repository &&
          row.label === label,
      )
      .sort((left, right) => left.hour.localeCompare(right.hour));

    if (elements.range.value !== "all" && rows.length) {
      const cutoff =
        Date.parse(manifest.latest_hour) -
        Number(elements.range.value) * 24 * 60 * 60 * 1000;
      rows = rows.filter((row) => Date.parse(row.hour) > cutoff);
    }
    selectedRows = rows;
    render();
  } catch (error) {
    if (version !== refreshVersion) {
      return;
    }
    selectedRows = [];
    render();
    elements.status.textContent = error.message;
  }
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) {
    return "-";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  if (seconds < 3600) {
    const minutes = seconds / 60;
    return `${minutes.toFixed(minutes < 10 ? 1 : 0)}m`;
  }
  return `${(seconds / 3600).toFixed(1)}h`;
}

function niceStep(value) {
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  if (normalized <= 1) {
    return magnitude;
  }
  if (normalized <= 2) {
    return 2 * magnitude;
  }
  if (normalized <= 5) {
    return 5 * magnitude;
  }
  return 10 * magnitude;
}

function renderChart() {
  const width = 1040;
  const height = 480;
  const left = 68;
  const right = 22;
  const top = 22;
  const bottom = 66;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;

  if (!selectedRows.length) {
    elements.chart.innerHTML = `<text class="empty-label" x="${width / 2}" y="${height / 2}" text-anchor="middle">No queue measurements match these filters.</text>`;
    return;
  }

  const maxValue = Math.max(...selectedRows.map((row) => row.p95), 1);
  const tickStep = niceStep(maxValue / 4);
  const maxAxis = Math.ceil(maxValue / tickStep) * tickStep;
  const y = (value) => top + ((maxAxis - value) / maxAxis) * plotHeight;
  const x = (index) =>
    left +
    (selectedRows.length === 1
      ? plotWidth / 2
      : (index / (selectedRows.length - 1)) * plotWidth);
  const ticks = Array.from(
    { length: Math.round(maxAxis / tickStep) + 1 },
    (_, index) => tickStep * index,
  );
  const grid = ticks
    .map(
      (value) =>
        `<line class="gridline" x1="${left}" x2="${width - right}" y1="${y(value)}" y2="${y(value)}"></line>` +
        `<text class="axis-label" x="${left - 10}" y="${y(value) + 4}" text-anchor="end">${formatDuration(value)}</text>`,
    )
    .join("");
  const labelStep = Math.max(1, Math.ceil(selectedRows.length / 12));
  const labels = selectedRows
    .map((row, index) =>
      index % labelStep === 0
        ? `<text class="x-label" x="${x(index)}" y="${height - 28}" text-anchor="middle">${row.hour.slice(5, 13).replace("T", " ")}</text>`
        : "",
    )
    .join("");
  const line = (key) =>
    selectedRows
      .map(
        (row, index) =>
          `${index ? "L" : "M"} ${x(index).toFixed(1)} ${y(row[key]).toFixed(1)}`,
      )
      .join(" ");
  const dots = (key) =>
    selectedRows
      .map(
        (row, index) =>
          `<circle class="dot-${key}" cx="${x(index)}" cy="${y(row[key])}" r="3.5">` +
          `<title>${row.hour} | ${key}: ${formatDuration(row[key])} | n=${row.count.toLocaleString()}</title></circle>`,
      )
      .join("");

  elements.chart.innerHTML =
    grid +
    `<line class="axis" x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}"></line>` +
    `<line class="axis" x1="${left}" x2="${width - right}" y1="${height - bottom}" y2="${height - bottom}"></line>` +
    `<path class="line-p95" d="${line("p95")}"></path>` +
    `<path class="line-p90" d="${line("p90")}"></path>` +
    `<path class="line-p50" d="${line("p50")}"></path>` +
    dots("p95") +
    dots("p90") +
    dots("p50") +
    labels;
}

function render() {
  renderChart();
  if (selectedRows.length) {
    elements.status.textContent =
      `${selectedRows[0].hour} through ${selectedRows.at(-1).hour}`;
  } else {
    elements.status.textContent = "No queue measurements match these filters.";
  }
}

async function initialize() {
  try {
    const response = await fetch(`${DATA_ROOT}/manifest.json`);
    if (!response.ok) {
      throw new Error("Unable to load the queue report manifest.");
    }
    manifest = await response.json();
    updateDependentFilters();
    elements.exclusions.textContent =
      `${manifest.null_queue_records.toLocaleString()} records have no runner-assigned queue time. ` +
      `${manifest.negative_queue_records.toLocaleString()} records have negative timestamp anomalies.`;
    elements.updatedAt.textContent = manifest.updated_at
      ? `Updated ${manifest.updated_at}`
      : "No queue data collected yet";
    await refreshData();
  } catch (error) {
    elements.status.textContent = error.message;
  }
}

elements.host.addEventListener("change", () => {
  updateDependentFilters();
  refreshData();
});
elements.label.addEventListener("change", refreshData);
elements.repository.addEventListener("change", refreshData);
elements.range.addEventListener("change", refreshData);

initialize();
