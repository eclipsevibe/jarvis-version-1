'use strict';
let port=null,retry=1000;
const cancelled=new Set();
async function operation(message){
  if(Date.now()>message.expires*1000)throw new Error('Request expired; no tabs were closed.');
  if(message.action==='list'){
    const tabs=await chrome.tabs.query({});
    if(tabs.length>2000)throw new Error('Too many tabs; narrow the request before using this helper.');
    return tabs.map(t=>({id:t.id,window:t.windowId,title:t.title||'',url:t.url||'',active:t.active}));
  }
  if(message.action!=='close'||!Array.isArray(message.tabs)||message.tabs.length>2000)throw new Error('Unsupported browser operation.');
  let closed=0,skipped=0;
  for(const expected of message.tabs){
    if(cancelled.has(message.id)||Date.now()>message.expires*1000){skipped++;continue;}
    try{
      const current=await chrome.tabs.get(expected.id);
      if(cancelled.has(message.id)||Date.now()>message.expires*1000){skipped++;continue;}
      if(current.url!==expected.url||current.title!==expected.title||current.windowId!==expected.window){skipped++;continue;}
      await chrome.tabs.remove(expected.id);closed++;
    }catch{skipped++;}
  }
  return {closed,skipped};
}
function connect(){
  if(port)return;
  try{
    port=chrome.runtime.connectNative('com.jarvis.browser_control');
    port.onMessage.addListener(async message=>{
      if(message.action==='cancel'){cancelled.add(message.cancel);setTimeout(()=>cancelled.delete(message.cancel),30000);return;}
      const source=port;
      try{const result=await operation(message);source?.postMessage({id:message.id,ok:true,result});retry=1000;}
      catch(error){try{source?.postMessage({id:message.id,ok:false,error:error.message});}catch{}}
    });
    port.onDisconnect.addListener(()=>{void chrome.runtime.lastError;port=null;setTimeout(connect,retry);retry=Math.min(30000,retry*2);});
  }catch{port=null;setTimeout(connect,retry);}
}
chrome.runtime.onStartup.addListener(connect);chrome.runtime.onInstalled.addListener(connect);connect();
