const initSqlJs = require('sql.js');
const fs = require('fs');

async function main() {
  const SQL = await initSqlJs();

  // Query tasks-index
  const TASKS_PATH = String.raw`C:\Users\v_wenloliu\.zcode\v2\tasks-index.sqlite`;
  const tasksData = fs.readFileSync(TASKS_PATH);
  const tasksDb = new SQL.Database(tasksData);

  const tables = tasksDb.exec("SELECT name FROM sqlite_master WHERE type='table'");
  console.log('=== tasks-index tables ===');
  if (tables.length > 0) console.log(tables[0].values);

  const schemaTasks = tasksDb.exec("SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'");
  console.log('\n=== tasks table schema ===');
  if (schemaTasks.length > 0) {
    const cols = tasksDb.exec("PRAGMA table_info(tasks)");
    if (cols.length > 0) {
      cols[0].columns.forEach(c => console.log(c));
    }
  }

  // Get all data from tasks
  const tasks = tasksDb.exec("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 50");
  if (tasks.length > 0) {
    console.log('\n=== tasks data (top 50 by updated) ===');
    console.log('Columns:', tasks[0].columns);
    for (const r of tasks[0].values) {
      console.log(r);
    }
  }

  // Get distinct agents
  const agents = tasksDb.exec("SELECT DISTINCT agent, provider FROM tasks");
  if (agents.length > 0) {
    console.log('\n=== distinct agents ===');
    agents[0].values.forEach(r => console.log(r));
  }

  // Check for yunshuiyao related
  const yunTask = tasksDb.exec("SELECT * FROM tasks WHERE title LIKE '%yunshuiyao%' OR title LIKE '%云水遥%'");
  if (yunTask.length > 0) {
    console.log('\n=== yunshuiyao tasks ===');
    yunTask[0].values.forEach(r => console.log(r));
  } else {
    console.log('\nNo yunshuiyao tasks found by title');
  }
}

main().catch(e => console.error(e));
