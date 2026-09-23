const ALL = "*";
const DATA_ROOT = "data";
const PERCENTILES = {
  p50: 0.5,
  p90: 0.9,
  p95: 0.95,
  p99: 0.99,
};
const elements = {
  chart: document.querySelector("#chart"),
  dataBody: document.querySelector("#hourly-data-body"),
  status: document.querySelector("#chart-status"),
  host: document.querySelector("#runner-host"),
  label: document.querySelector("#runner-label"),
  repository: document.querySelector("#repository"),
  start: document.querySelector("#start-date"),
  end: document.querySelector("#end-date"),
  updatedAt: document.querySelector("#updated-at"),
  rangeP50: document.querySelector("#range-p50"),
  rangeP90: document.querySelector("#range-p90"),
  rangeP95: document.querySelector("#range-p95"),
  rangeP99: document.querySelector("#range-p99"),
};

let manifest;
let selectedRows = [];
let selectedSummary;
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

function hasOption(select, value) {
  return Array.from(select.options).some((option) => option.value === value);
}

function restoreFiltersFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const host = params.get("host");
  if (host && hasOption(elements.host, host)) {
    elements.host.value = host;
  }
  updateDependentFilters();

  for (const [name, select] of [
    ["label", elements.label],
    ["repository", elements.repository],
  ]) {
    const value = params.get(name);
    if (value && hasOption(select, value)) {
      select.value = value;
    }
  }

  if (!manifest.dates.length) {
    elements.start.disabled = true;
    elements.end.disabled = true;
    return;
  }
  const firstDate = manifest.dates[0];
  const lastDate = manifest.dates.at(-1);
  const defaultStart = manifest.dates.at(-7) || firstDate;
  for (const input of [elements.start, elements.end]) {
    input.min = firstDate;
    input.max = lastDate;
  }
  const start = params.get("start");
  const end = params.get("end");
  elements.start.value = isValidDate(start) ? start : defaultStart;
  elements.end.value = isValidDate(end) ? end : lastDate;
  if (elements.start.value > elements.end.value) {
    elements.start.value = defaultStart;
    elements.end.value = lastDate;
  }
}

function isValidDate(value) {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value || "")
    ? new Date(`${value}T00:00:00Z`)
    : undefined;
  return (
    manifest.dates.length > 0 &&
    Number.isFinite(date?.getTime()) &&
    date.toISOString().slice(0, 10) === value &&
    value >= manifest.dates[0] &&
    value <= manifest.dates.at(-1)
  );
}

function persistFiltersToQuery() {
  const url = new URL(window.location.href);
  url.searchParams.set("host", elements.host.value);
  if (elements.label.value === ALL) {
    url.searchParams.delete("label");
  } else {
    url.searchParams.set("label", elements.label.value);
  }
  if (elements.repository.value === ALL) {
    url.searchParams.delete("repository");
  } else {
    url.searchParams.set("repository", elements.repository.value);
  }
  for (const [name, input] of [
    ["start", elements.start],
    ["end", elements.end],
  ]) {
    if (input.value) {
      url.searchParams.set(name, input.value);
    } else {
      url.searchParams.delete(name);
    }
  }
  url.searchParams.delete("range");
  window.history.replaceState(null, "", url);
}

function datesForRange() {
  if (!elements.start.value || !elements.end.value) {
    return [];
  }
  return manifest.dates.filter(
    (date) => date >= elements.start.value && date <= elements.end.value,
  );
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
    selectedRows = partitions
      .flatMap((partition) => partition.series)
      .filter(
        (row) =>
          row.host === host &&
          row.repository === repository &&
          row.label === label,
      )
      .sort((left, right) => left.hour.localeCompare(right.hour));
    selectedSummary = summarizePartitions(partitions, host, repository, label);
    render();
  } catch (error) {
    if (version !== refreshVersion) {
      return;
    }
    selectedRows = [];
    selectedSummary = undefined;
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

function formatExactDuration(seconds) {
  return Number.isFinite(seconds) ? `${seconds}s` : "-";
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

function summarizePartitions(partitions, host, repository, label) {
  const histogram = new Map();
  for (const partition of partitions) {
    const summary = partition.summaries.find(
      (row) =>
        row.host === host &&
        row.repository === repository &&
        row.label === label,
    );
    if (!summary) {
      continue;
    }
    for (const [value, count] of summary.histogram) {
      histogram.set(value, (histogram.get(value) || 0) + count);
    }
  }
  const values = Array.from(histogram.entries()).sort(
    (left, right) => left[0] - right[0],
  );
  const count = values.reduce((total, entry) => total + entry[1], 0);
  if (!count) {
    return undefined;
  }
  return Object.fromEntries(
    Object.entries(PERCENTILES).map(([name, fraction]) => [
      name,
      percentile(values, count, fraction),
    ]),
  );
}

function percentile(values, count, fraction) {
  const position = (count - 1) * fraction;
  const lowerIndex = Math.floor(position);
  const upperIndex = Math.ceil(position);
  const lower = valueAtIndex(values, lowerIndex);
  const upper = valueAtIndex(values, upperIndex);
  return (
    Math.round(
      (lower + (upper - lower) * (position - lowerIndex)) * 1000,
    ) / 1000
  );
}

function valueAtIndex(values, index) {
  let seen = 0;
  for (const [value, count] of values) {
    seen += count;
    if (index < seen) {
      return value;
    }
  }
  throw new Error(`Percentile index ${index} exceeds histogram size ${seen}`);
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

  const maxValue = Math.max(...selectedRows.map((row) => row.p99), 1);
  const tickStep = niceStep(maxValue / 4);
  const maxAxis = Math.ceil(maxValue / tickStep) * tickStep;
  const y = (value) => top + ((maxAxis - value) / maxAxis) * plotHeight;
  const timestamps = selectedRows.map((row) => Date.parse(row.hour));
  const firstTimestamp = timestamps[0];
  const elapsed = timestamps.at(-1) - firstTimestamp;
  const x = (index) =>
    left +
    (elapsed === 0
      ? plotWidth / 2
      : ((timestamps[index] - firstTimestamp) / elapsed) * plotWidth);
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
          `<title>${row.hour} | ${key}: ${formatExactDuration(row[key])} | n=${row.count.toLocaleString()}</title></circle>`,
      )
      .join("");

  elements.chart.innerHTML =
    grid +
    `<line class="axis" x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}"></line>` +
    `<line class="axis" x1="${left}" x2="${width - right}" y1="${height - bottom}" y2="${height - bottom}"></line>` +
    `<path class="line-p99" d="${line("p99")}"></path>` +
    `<path class="line-p95" d="${line("p95")}"></path>` +
    `<path class="line-p90" d="${line("p90")}"></path>` +
    `<path class="line-p50" d="${line("p50")}"></path>` +
    dots("p99") +
    dots("p95") +
    dots("p90") +
    dots("p50") +
    labels;
}

function renderRangeSummary() {
  elements.rangeP50.textContent = formatExactDuration(selectedSummary?.p50);
  elements.rangeP90.textContent = formatExactDuration(selectedSummary?.p90);
  elements.rangeP95.textContent = formatExactDuration(selectedSummary?.p95);
  elements.rangeP99.textContent = formatExactDuration(selectedSummary?.p99);
}

function renderAccessibleData() {
  const rows = selectedRows.map((row) => {
    const tableRow = document.createElement("tr");
    for (const value of [
      row.hour,
      row.count.toLocaleString(),
      formatExactDuration(row.p50),
      formatExactDuration(row.p90),
      formatExactDuration(row.p95),
      formatExactDuration(row.p99),
    ]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      tableRow.append(cell);
    }
    return tableRow;
  });
  if (!rows.length) {
    const tableRow = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = "No queue measurements match these filters.";
    tableRow.append(cell);
    rows.push(tableRow);
  }
  elements.dataBody.replaceChildren(...rows);
}

function render() {
  renderRangeSummary();
  renderChart();
  renderAccessibleData();
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
    restoreFiltersFromQuery();
    persistFiltersToQuery();
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
  persistFiltersToQuery();
  refreshData();
});
elements.label.addEventListener("change", () => {
  persistFiltersToQuery();
  refreshData();
});
elements.repository.addEventListener("change", () => {
  persistFiltersToQuery();
  refreshData();
});

function updateDateRange(changed) {
  if (!isValidDate(elements.start.value)) {
    elements.start.value = manifest.dates.at(-7) || manifest.dates[0];
  }
  if (!isValidDate(elements.end.value)) {
    elements.end.value = manifest.dates.at(-1);
  }
  if (elements.start.value > elements.end.value) {
    if (changed === "start") {
      elements.end.value = elements.start.value;
    } else {
      elements.start.value = elements.end.value;
    }
  }
  persistFiltersToQuery();
  refreshData();
}

elements.start.addEventListener("change", () => updateDateRange("start"));
elements.end.addEventListener("change", () => updateDateRange("end"));

initialize();
