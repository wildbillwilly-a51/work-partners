import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const here=dirname(fileURLToPath(import.meta.url));
const client=new Client({name:'portable-doctor',version:'1'});
const transport=new StdioClientTransport({command:process.execPath,args:[resolve(here,'server.mjs')],env:{...process.env,WORK_PARTNERS_CONTEXT_CONFIG:resolve(process.argv[2])},stderr:'pipe'});
try{
 await client.connect(transport);const tools=await client.listTools();
 const result=await client.callTool({name:'list_projects',arguments:{}});if(result.isError)throw Error('project_discovery_failed');
 const data=JSON.parse(result.content[0].text);
 console.log(JSON.stringify({tools:tools.tools.map(t=>t.name),projects:data.projects.length}));
}finally{await client.close();}
