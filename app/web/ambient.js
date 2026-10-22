(function(){
'use strict';
const canvas=document.getElementById('ambient-canvas');if(!canvas)return;
let gl;try{gl=canvas.getContext('webgl',{alpha:true,antialias:false,depth:false,powerPreference:'low-power'});}catch{}if(!gl)return;
const vertex='attribute vec2 position;void main(){gl_Position=vec4(position,0.,1.);}';
const fragment=`precision mediump float;
uniform vec2 resolution;uniform vec2 pointer;uniform float time;uniform vec3 accent;uniform float dark;
void main(){
vec2 uv=gl_FragCoord.xy/resolution;vec2 p=uv;
p.x+=.07*sin(p.y*5.+time*.12);p.y+=.05*cos(p.x*6.-time*.1);
vec2 a=vec2(.75+.12*sin(time*.08),.72+.12*cos(time*.11))+pointer*.025;
float glow=exp(-5.*length((p-a)*vec2(1.,1.4)));
float ribbon=pow(max(0.,1.-abs(p.y-.65-.13*sin(p.x*4.+time*.1))*3.),3.);
float second=exp(-6.*length(p-vec2(.15,.25)));
vec3 tint=mix(accent,vec3(.35,.48,.85),second*.45);
float alpha=(glow*.24+ribbon*.045+second*.08)*mix(.55,.85,dark);
gl_FragColor=vec4(tint*alpha,alpha);
}`;
function shader(type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error('shader');return s;}
let program;try{program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vertex));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))return;}catch{return;}
gl.useProgram(program);const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
const pos=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(pos);gl.vertexAttribPointer(pos,2,gl.FLOAT,false,0,0);
const uniforms=Object.fromEntries(['resolution','pointer','time','accent','dark'].map(k=>[k,gl.getUniformLocation(program,k)]));
let frame=0,last=0,elapsed=0,active=false,lost=false,point=[0,0];const reduced=matchMedia('(prefers-reduced-motion: reduce)');
function draw(now){
 if(lost)return;
 const root=document.documentElement,ratio=Math.min(devicePixelRatio||1,1.25),w=Math.round(innerWidth*ratio),h=Math.round(innerHeight*ratio);
 if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;gl.viewport(0,0,w,h);}
 const value=getComputedStyle(root).getPropertyValue('--green').trim();let color=[.3,.6,.5];try{color=SupplyTheme.rgb(value);}catch{}
 gl.uniform2f(uniforms.resolution,w,h);gl.uniform2f(uniforms.pointer,...point);gl.uniform1f(uniforms.time,elapsed);gl.uniform3f(uniforms.accent,...color);gl.uniform1f(uniforms.dark,root.dataset.theme==='dark'?1:0);gl.drawArrays(gl.TRIANGLES,0,6);
}
function tick(now){if(!active)return;if(now-last>=50){elapsed+=Math.min((now-last)/1000,.1);last=now;draw(now);}frame=requestAnimationFrame(tick);}
function refresh(){cancelAnimationFrame(frame);active=false;last=performance.now();const d=document.documentElement.dataset;if(document.hidden||d.effects==='off'||lost)return;draw(last);active=d.effects==='aurora'&&d.motion!=='off'&&!reduced.matches;if(active)frame=requestAnimationFrame(tick);}
new MutationObserver(refresh).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','data-accent','data-effects','data-motion','style']});
addEventListener('resize',refresh,{passive:true});document.addEventListener('visibilitychange',refresh);reduced.addEventListener('change',refresh);
addEventListener('pointermove',event=>{point=[event.clientX/innerWidth-.5,.5-event.clientY/innerHeight];},{passive:true});
canvas.addEventListener('webglcontextlost',()=>{lost=true;active=false;cancelAnimationFrame(frame);canvas.style.display='none';});
refresh();
})();
