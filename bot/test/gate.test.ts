/**
 * Offline unit tests for the SQL gate (no database, no LLM).
 * Run: npm test   (node --import tsx --test test/*.test.ts)
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { gateSql } from "../src/tools/sqlGate";

const opts = {
  schema: "poc_icla",
  views: [
    "Salesforce_Data", "Leads", "Appointments", "Leads_Journey", "Lead Milestones", "Opportunities to Appointments",
    "dim_projects", "dim_designers", "dim_entity", "DateTable", "SoldDateTable", "LS Group Sort", "Source_Sort",
    "Lead Source Channels", "report_scorecard", "report_designer_leaderboard",
  ],
};

// [label, sql, expected reject code]
const HOSTILE: [string, string, string][] = [
  ["stacked drop", "SELECT 1; DROP TABLE Salesforce_Data", "multi_statement"],
  ["stacked select", "SELECT 1; SELECT 2", "multi_statement"],
  ["stacked after trailing comment", "SELECT 1 FROM Leads; -- x\nDELETE FROM Leads", "multi_statement"],
  ["delete", "DELETE FROM Salesforce_Data", "not_select"],
  ["update", "UPDATE Salesforce_Data SET `Amount` = 0", "not_select"],
  ["insert", "INSERT INTO Leads (`Lead ID`) VALUES ('x')", "into"],
  ["replace", "REPLACE INTO Leads VALUES ('x')", "into"],
  ["drop", "DROP VIEW Salesforce_Data", "not_select"],
  ["create", "CREATE TABLE x (a INT)", "not_select"],
  ["alter", "ALTER TABLE Leads ADD COLUMN x INT", "not_select"],
  ["truncate", "TRUNCATE TABLE Leads", "not_select"],
  ["grant", "GRANT ALL ON *.* TO 'poc_icla'@'localhost'", "variable"],
  ["set session", "SET SESSION TRANSACTION READ WRITE", "parse"],
  ["set sql_mode", "SET sql_mode = ''", "not_select"],
  ["call", "CALL some_proc()", "not_select"],
  ["show tables", "SHOW TABLES", "not_select"],
  ["sleep", "SELECT SLEEP(20)", "function"],
  ["sleep in where", "SELECT * FROM Leads WHERE SLEEP(5) = 0", "function"],
  ["sleep backticked", "SELECT `sleep`(5) FROM Leads", "function"],
  ["sleep spaced", "SELECT SLEEP /* x */ (5)", "function"],
  ["benchmark", "SELECT BENCHMARK(100000000, MD5('a'))", "function"],
  ["get_lock", "SELECT GET_LOCK('x', 10)", "function"],
  ["load_file", "SELECT LOAD_FILE('C:/Windows/win.ini')", "function"],
  ["extractvalue exfil", "SELECT EXTRACTVALUE(1, CONCAT(0x5c, (SELECT 1)))", "function"],
  ["version probe", "SELECT VERSION()", "function"],
  ["current_user bare", "SELECT CURRENT_USER", "function"],
  ["database probe", "SELECT DATABASE()", "function"],
  ["into outfile", "SELECT * FROM Leads INTO OUTFILE 'C:/temp/x.csv'", "into"],
  ["into dumpfile", "SELECT `Lead ID` FROM Leads LIMIT 1 INTO DUMPFILE 'C:/temp/x.bin'", "into"],
  ["into variable", "SELECT COUNT(*) INTO @n FROM Leads", "variable"],
  ["user variable assign", "SELECT @a := COUNT(*) FROM Leads", "variable"],
  ["system variable read", "SELECT @@version", "variable"],
  ["assign without at", "SELECT x := 1", "variable"],
  ["for update", "SELECT * FROM Leads FOR UPDATE", "locking"],
  ["for share", "SELECT * FROM Leads FOR SHARE", "locking"],
  ["lock in share mode", "SELECT * FROM Leads LOCK IN SHARE MODE", "locking"],
  ["optimizer hint", "SELECT /*+ MAX_EXECUTION_TIME(1000000) */ * FROM Leads", "hint_comment"],
  ["executable comment", "SELECT 1 /*!50000 , SLEEP(5) */", "executable_comment"],
  ["information_schema tables", "SELECT * FROM information_schema.TABLES", "system_schema"],
  ["information_schema backticked", "SELECT * FROM `information_schema`.`COLUMNS`", "system_schema"],
  ["information_schema double-quoted", "SELECT * FROM \"information_schema\".\"SCHEMATA\"", "system_schema"],
  ["mysql.user", "SELECT user, authentication_string FROM mysql.user", "system_schema"],
  ["performance_schema", "SELECT * FROM performance_schema.threads", "system_schema"],
  ["sys schema", "SELECT * FROM sys.processlist", "system_schema"],
  ["other franchise schema", "SELECT SUM(`Amount`) FROM poc_icau.Salesforce_Data", "table"],
  ["other franchise in subquery", "SELECT (SELECT COUNT(*) FROM poc_icch.Leads) AS n FROM Leads", "table"],
  ["other franchise via union", "SELECT `Designer` FROM Salesforce_Data UNION SELECT `Designer` FROM poc_icau.Salesforce_Data", "table"],
  ["production gold table", "SELECT * FROM icall_gold.fact_sales", "table"],
  ["raw table not a view", "SELECT * FROM fact_sales", "table"],
  ["procedure analyse", "SELECT * FROM Leads PROCEDURE ANALYSE()", "procedure"],
  ["empty", "   ", "empty"],
  ["unterminated string", "SELECT 'abc FROM Leads", "lex"],
  ["unterminated comment", "SELECT 1 /* never closed", "lex"],
  ["not sql", "EVALUATE ROW(\"x\", [Revenue Sold])", "parse"],
  ["handler", "HANDLER Leads OPEN", "parse"],
  ["load data", "LOAD DATA INFILE 'x' INTO TABLE Leads", "into"],
  ["semicolon then comment then statement", "SELECT 1 FROM Leads /* ; */; SELECT 2", "multi_statement"],
  ["random_bytes", "SELECT RANDOM_BYTES(1024)", "function"],
  ["internal function", "SELECT INTERNAL_TABLE_ROWS('a','b','c',1,1,1,1,1,1)", "function"],
];

const VALID: [string, string][] = [
  ["simple sum", "SELECT SUM(`Amount`) AS `Sold` FROM Salesforce_Data WHERE `Revised Stage` = 'Sold'"],
  ["trailing semicolon", "SELECT COUNT(*) AS n FROM Leads;"],
  ["group by designer", "SELECT `Designer`, SUM(CASE WHEN `Revised Stage` = 'Sold' THEN `Amount` END) AS `Sold` FROM Salesforce_Data WHERE YEAR(`Original Project Sold Date`) = 2026 GROUP BY `Designer` ORDER BY `Sold` DESC LIMIT 10"],
  ["qualified own schema", "SELECT COUNT(*) FROM poc_icla.Leads"],
  ["view with spaces", "SELECT COUNT(*) FROM `Lead Milestones` WHERE `Exclude From Journey` = 0"],
  ["join + cte", "WITH s AS (SELECT `Project Name`, `Amount` FROM Salesforce_Data WHERE `Revised Stage` = 'Proposed') SELECT s.`Project Name`, s.`Amount`, MAX(a.`Activity Date and Time`) AS `Last Activity` FROM s LEFT JOIN Appointments a ON a.`Project Name` = s.`Project Name` GROUP BY s.`Project Name`, s.`Amount`"],
  ["recursive cte", "WITH RECURSIVE m(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM m WHERE n < 12) SELECT n FROM m"],
  ["window function", "SELECT `Designer`, SUM(`Amount`) AS s, RANK() OVER (ORDER BY SUM(`Amount`) DESC) AS r FROM Salesforce_Data GROUP BY `Designer`"],
  ["median pattern", "WITH x AS (SELECT `Days to Sold` AS d, ROW_NUMBER() OVER (ORDER BY `Days to Sold`) AS rn, COUNT(*) OVER () AS c FROM `Lead Milestones` WHERE `Days to Sold` >= 0) SELECT AVG(d) AS `Median days` FROM x WHERE rn IN (FLOOR((c + 1) / 2), CEIL((c + 1) / 2))"],
  ["union of views", "SELECT 'Sold' AS k, COUNT(*) AS n FROM Salesforce_Data WHERE `Revised Stage` = 'Sold' UNION ALL SELECT 'Leads', COUNT(*) FROM Leads"],
  ["double-quoted identifiers", "SELECT \"Designer\", COUNT(*) AS n FROM \"Salesforce_Data\" GROUP BY \"Designer\""],
  ["like filter", "SELECT COUNT(*) FROM Salesforce_Data WHERE `Lead Source` LIKE '%self%'"],
  ["string containing keywords", "SELECT COUNT(*) FROM Leads WHERE `Lead Status` = 'into; sleep(5) for update @x'"],
  ["comment line", "-- sold this year\nSELECT SUM(`Amount`) FROM Salesforce_Data WHERE `Revised Stage` = 'Sold'"],
  ["block comment", "SELECT /* total */ COUNT(*) FROM Leads"],
  ["date table", "SELECT d.`Year`, COUNT(*) FROM Salesforce_Data s JOIN DateTable d ON d.`Date` = s.`Created Date` GROUP BY d.`Year`"],
  ["closing rate window", "SELECT LEAST(1, COUNT(CASE WHEN `Revised Stage` = 'Sold' AND `Original Project Sold Date` BETWEEN '2026-01-01' AND '2026-11-07' THEN 1 END) / NULLIF(COUNT(CASE WHEN `Created Date` BETWEEN '2026-01-01' AND '2026-11-07' THEN 1 END), 0)) AS `Closing Rate` FROM Salesforce_Data"],
  ["derived table", "SELECT t.`Designer`, t.n FROM (SELECT `Designer`, COUNT(*) AS n FROM Salesforce_Data GROUP BY `Designer`) t WHERE t.n >= 30"],
  ["exists subquery", "SELECT COUNT(*) FROM Salesforce_Data s WHERE EXISTS (SELECT 1 FROM Appointments a WHERE a.`Project Name` = s.`Project Name`)"],
  ["weekly table", "SELECT * FROM report_scorecard WHERE report_date = (SELECT MAX(report_date) FROM report_scorecard) AND metric_key = 'sold_dollars'"],
  ["mixed case view name", "SELECT COUNT(*) FROM salesforce_data"],
  ["column named lock-ish in backticks", "SELECT `Is Real Appointment` AS `Lock` FROM Appointments LIMIT 5"],
];

describe("SQL gate rejects hostile SQL", () => {
  it(`has at least 40 hostile cases (${HOSTILE.length})`, () => assert.ok(HOSTILE.length >= 40));
  for (const [label, sql, code] of HOSTILE) {
    it(label, () => {
      const r = gateSql(sql, opts);
      assert.equal(r.ok, false, `expected rejection for: ${sql}`);
      if (!r.ok) assert.equal(r.code, code, `${label}: rejected with ${r.code} (${r.reason}), expected ${code}`);
    });
  }
});

describe("SQL gate accepts valid SELECTs", () => {
  it(`has at least 15 valid cases (${VALID.length})`, () => assert.ok(VALID.length >= 15));
  for (const [label, sql] of VALID) {
    it(label, () => {
      const r = gateSql(sql, opts);
      assert.equal(r.ok, true, r.ok ? "" : `${label}: rejected (${r.code}: ${r.reason})`);
    });
  }
  it("rewrites double-quoted identifiers to backticks and strips the trailing semicolon", () => {
    const r = gateSql('SELECT "Revised Stage", COUNT(*) FROM "Salesforce_Data" GROUP BY "Revised Stage";', opts);
    assert.ok(r.ok);
    if (r.ok) assert.equal(r.sql, "SELECT `Revised Stage`, COUNT(*) FROM `Salesforce_Data` GROUP BY `Revised Stage`");
  });
  it("reports referenced views and CTEs", () => {
    const r = gateSql("WITH s AS (SELECT * FROM Salesforce_Data) SELECT * FROM s JOIN Leads l ON 1 = 1", opts);
    assert.ok(r.ok);
    if (r.ok) {
      assert.deepEqual([...r.tables].sort(), ["Leads", "Salesforce_Data"]);
      assert.deepEqual(r.ctes, ["s"]);
    }
  });
});
