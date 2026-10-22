import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const source=path.join(root,'app','web');
const destination=path.join(root,'mobile','www');
await fs.mkdir(destination,{recursive:true});
for(const file of await fs.readdir(source)) {
  if(file==='sw.js') continue;
  await fs.copyFile(path.join(source,file),path.join(destination,file));
}
const base=process.env.SUPPLY_MATCH_MOBILE_API_URL || '';
if(base) {
  const url=new URL(base);
  if(url.protocol!=='https:' || url.username || url.password || url.search || url.hash) throw new Error('MOBILE_API_URL must be a public HTTPS origin');
  if(url.pathname!=='/') throw new Error('MOBILE_API_URL must not contain a path');
}
const filename=path.join(destination,'index.html');
let html=await fs.readFile(filename,'utf8');
html=html.replace('<meta name="api-base" content="">',`<meta name="api-base" content="${base.replace(/[&"<>]/g,'')}">`);
html=html.replace('<script src="/theme-utils.js"','<script src="/native-bridge.js" defer></script><script src="/theme-utils.js"');
await fs.writeFile(filename,html);
await build({entryPoints:[path.join(root,'mobile','native-bridge.js')],bundle:true,format:'iife',platform:'browser',target:'es2020',outfile:path.join(destination,'native-bridge.js')});
console.log(base?'Native assets prepared with HTTPS API endpoint.':'Native assets prepared. Enter the server address on the connection screen.');
