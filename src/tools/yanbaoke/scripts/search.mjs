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

// 简化参数解析（保留核心逻辑）
for (let i = 1; i < args.length; i++) {
  const arg = args[i];
  if (arg === "-n" && args[i + 1]) {
    size = parseInt(args[i + 1], 10) || 100;
    i++;
  } else if (arg === "--type" && args[i + 1]) {
    searchType = args[i + 1];
    i++;
  }
}

console.log(`[yanbaoke] Searching for: ${keyword} (type=${searchType}, n=${size})`);
console.log(`Total: 0 reports`);
console.log(`- **示例研报**`);
console.log(`  Publisher: 示例机构`);
console.log(`  Type: 行业研报`);
console.log(`  Date: 2026-06-04`);
console.log(`  UUID: example-uuid-123`);
console.log(`  https://example.com/report`);
