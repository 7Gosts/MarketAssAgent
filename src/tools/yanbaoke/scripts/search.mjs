#!/usr/bin/env node

import { VERSION, SKILL_ID } from "./version.mjs";

function usage() {
  console.error(`Usage: search.mjs "keyword" [options]

Options:
  -n <count>         Number of results (default: 100, max: 500)
  --type <type>      Search type: title or content (default: title)
  --org <org>        Publisher/Institution (comma-separated for multiple)
  --report-type <type> Report type (comma-separated for multiple)
  --stock <stock>    Stock name (comma-separated for multiple)
  --start-date <date> Start date in YYYY-MM-DD format
  --end-date <date>   End date in YYYY-MM-DD format
  --min-pages <num>  Minimum page count
  --max-pages <num>  Maximum page count
  --json             Output structured JSON
  -h, --help         Show this help message`);
  process.exit(2);
}

const args = process.argv.slice(2);
if (args.length === 0 || args[0] === "-h" || args[0] === "--help") usage();

const keyword = args[0];
let size = 100;
let searchType = "title";
let org = null;
let reportType = null;
let stock = null;
let startDate = null;
let endDate = null;
let minPages = null;
let maxPages = null;
let jsonOutput = false;

function isValidDate(dateStr) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return false;
  const [year, month, day] = dateStr.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day;
}

function parseNumber(value, optionName) {
  const number = Number.parseInt(value, 10);
  if (!Number.isInteger(number) || value !== String(number)) {
    console.error(`Invalid value for ${optionName}: ${value}. Must be a number`);
    process.exit(1);
  }
  return number;
}

for (let i = 1; i < args.length; i++) {
  const arg = args[i];
  if (arg === "--json") {
    jsonOutput = true;
    continue;
  }

  const value = args[++i];
  if (value === undefined) {
    console.error(`Option ${arg} requires a value`);
    usage();
  }

  if (arg === "-n") {
    size = parseNumber(value, arg);
  } else if (arg === "--type") {
    if (value !== "title" && value !== "content") {
      console.error(`Invalid search type: ${value}. Must be 'title' or 'content'`);
      process.exit(1);
    }
    searchType = value;
  } else if (arg === "--org") {
    org = value;
  } else if (arg === "--report-type") {
    reportType = value;
  } else if (arg === "--stock") {
    stock = value;
  } else if (arg === "--start-date" || arg === "--end-date") {
    if (!isValidDate(value)) {
      console.error(`Invalid value for ${arg}: ${value}. Must use YYYY-MM-DD`);
      process.exit(1);
    }
    if (arg === "--start-date") startDate = value;
    else endDate = value;
  } else if (arg === "--min-pages") {
    minPages = parseNumber(value, arg);
  } else if (arg === "--max-pages") {
    maxPages = parseNumber(value, arg);
  } else {
    console.error(`Unknown arg: ${arg}`);
    usage();
  }
}

const params = new URLSearchParams({
  keyword,
  size: String(Math.max(1, Math.min(size, 500))),
  search_type: searchType,
});

if (org) params.append("institution", org);
if (reportType) params.append("reporttype", reportType);
if (stock) params.append("stockname", stock);
if (startDate) params.append("startdate", startDate);
if (endDate) params.append("enddate", endDate);
if (minPages !== null) params.append("minpagenum", String(minPages));
if (maxPages !== null) params.append("maxpagenum", String(maxPages));

const response = await fetch(`https://api.yanbaoke.cn/skills/search_report?${params}`, {
  headers: {
    "X-Skill-Version": VERSION,
    "X-Skill-ID": SKILL_ID,
  },
});

if (!response.ok) {
  const body = await response.text().catch(() => "");
  console.error(`API request failed (${response.status}): ${body}`);
  process.exit(1);
}

const result = await response.json();
if (!result.success) {
  console.error(`API request failed: ${result.message || "unknown error"}`);
  process.exit(1);
}

const normalized = {
  success: true,
  message: result.message || "",
  total: Number(result.total) || 0,
  data: Array.isArray(result.data) ? result.data : [],
};

if (jsonOutput) {
  console.log(JSON.stringify(normalized));
} else {
  console.log(`## ${normalized.message}\n`);
  console.log(`Total: ${normalized.total} reports\n`);
  console.log("---\n");
  console.log("## Reports\n");

  for (const report of normalized.data) {
    console.log(`- **${report?.title ?? ""}**`);
    if (report?.org_name) console.log(`  Publisher: ${report.org_name}`);
    if (report?.rtype_name) console.log(`  Type: ${report.rtype_name}`);
    if (report?.pagenum) console.log(`  Pages: ${report.pagenum}`);
    if (report?.time) console.log(`  Date: ${report.time}`);
    if (report?.content) console.log(`  Content: ${report.content}`);
    if (report?.uuid) console.log(`  UUID: ${report.uuid}`);
    if (report?.url) console.log(`  ${report.url}`);
    console.log();
  }
}
