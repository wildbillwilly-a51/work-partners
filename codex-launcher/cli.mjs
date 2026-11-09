import { readFileSync } from 'node:fs';
import { fromConfig } from './core.mjs';
import { ProjectAccess } from './project-access.mjs';
const [command,configPath,arg]=process.argv.slice(2);
if(!configPath || !['context','notes','save','projects','project-context','project-file','project-diff','project-save','project-notes','project-handoffs','project-stage-handoff'].includes(command)) throw Error('Usage: node cli.mjs COMMAND CONFIG [LIMIT|JSON_FILE]. General project commands accept a JSON argument file.');
const store=fromConfig(configPath);
try {
  let result;
  if(command==='context')result=store.context();else if(command==='notes')result=store.list(arg?Number(arg):5);else if(command==='save')result=store.save(JSON.parse(readFileSync(arg,'utf8').replace(/^\uFEFF/,'')));
  else{const access=new ProjectAccess(JSON.parse(readFileSync(configPath,'utf8').replace(/^\uFEFF/,'')),store,configPath);const args=arg?JSON.parse(readFileSync(arg,'utf8').replace(/^\uFEFF/,'')):{};const method={'projects':'projects','project-context':'context','project-file':'read','project-diff':'diff','project-save':'save','project-notes':'notes','project-handoffs':'handoffs','project-stage-handoff':'stageHandoff'}[command];result=access[method](args);}
  console.log(JSON.stringify(result,null,2));
}
finally { store.close(); }
