'use strict';
// 数据质控状态板：排序、结论、原因一句话、近 7 天小条、展开状态保留。
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check){for(let i=0;i<400;i++){if(check())return;await pause(5);}throw Error('Condition timed out');}

const run='2026-11-10T08:00:00+08:00';
const source=(id,name,arrival,issues=[],extra={})=>({id,name,kind:'actual',arrival:{latest_run_bjt:run,age_hours:12.9,warning_age_hours:18,expected_label:'依据本地下载清单检查。',...arrival},
 checks:{sampled_files:2,coverage_label:'数值抽查 2 份要素数据'},metrics:[],issues,...extra});
const days=cells=>cells.map((cell,index)=>({date:`2026-11-${String(4+index).padStart(2,'0')}`,runs:0,complete:0,pending:0,median_lag_hours:null,...cell}));
function payload(){
 return {schema:1,state:'ready',stale:false,ttl_seconds:300,job:{},
  history:{since_bjt:'2026-11-08T09:00:00+08:00',sources:{
   gfs:{days:days([{}, {}, {}, {}, {runs:4,complete:4,median_lag_hours:5.5},{runs:4,complete:3},{runs:2,complete:1,pending:1}]),
        recent:[{run_bjt:run,latest:true,available:60,expected:81,lag_hours:null,baseline:false},
                {run_bjt:'2026-11-10T02:00:00+08:00',complete_bjt:'2026-11-10T07:30:00+08:00',lag_hours:5.5,available:81,expected:81,baseline:false,latest:false}]}}},
  snapshot:{schema:1,generated_bjt:'2026-11-10T20:54:00+08:00',rules:[],limitations:['只代表抽查范围。'],excluded:{label:'演示模式不重复统计。'},
   sources:[
    source('gfs','NCEP-GFS',{status:'available',label:'已到报',available_frames:81,expected_frames:81,missing_leads:[]}),
    source('aifs','ECMWF-AIFS',{status:'available',label:'已到报',available_frames:40,expected_frames:40,missing_leads:[]},
     [{severity:'warning',code:'writer_errors',message:'1 个时效未入库，等待下载程序重试：+210h（累计降水比前一时效小）。',run_bjt:run}]),
    {id:'weathernext2',name:'WeatherNext 2',kind:'reserved',arrival:{status:'reserved',label:'未接入',expected_label:'本机没有 WN2 下载任务，不检查到报。'},checks:{sampled_files:0},metrics:[],issues:[]},
    source('ecmwf','ECMWF-IFS',{status:'partial',label:'部分到报',available_frames:63,expected_frames:65,missing_leads:[36,120]},
     [{severity:'warning',code:'missing_leads',message:'已发布时效范围内存在缺失预报时效。',run_bjt:run},
      {severity:'warning',code:'writer_errors',message:'2 个时效未入库，等待下载程序重试：+36h、+120h（累计降水比前一时效小）。',run_bjt:run}]),
    source('weathernext3','WeatherNext 3',{status:'unavailable',label:'资料清单暂不可用'},[{severity:'error',code:'catalog_invalid',message:'WN3 本地提交目录无法验证。'}]),
    source('observations','深圳格点实况',{status:'available',label:'实况已到报',available_frames:23,expected_frames:24,missing_leads:[],
     missing_valid_bjt:['2026-11-10T20:00:00+08:00'],pending_recent_hours:1,latest_run_bjt:'2026-11-10T19:00:00+08:00',age_hours:1.9},
     [{severity:'info',code:'observations_preparing',message:'近期 1 个实况小时尚未入库。'}])]}};
}

(async()=>{
 const dom=new JSDOM('<!doctype html><section id="quality-workspace"></section>',{runScripts:'outside-only',url:'http://localhost/Verification/test/'});
 const {window}=dom,calls=[];
 let next=payload();
 window.fetch=async(url,options={})=>{calls.push([options.method||'GET',String(url)]);const body=JSON.parse(JSON.stringify(next));return {ok:true,status:200,redirected:false,json:async()=>body};};
 window.eval(fs.readFileSync(path.join(__dirname,'quality.js'),'utf8'));
 const host=window.document.getElementById('quality-workspace');
 window.DataQuality.open();
 await until(()=>host.querySelectorAll('.qc-row').length===6);

 // 有问题的排最前：异常 → 需关注 → 正常 → 未接入；同级保持原顺序。
 const names=[...host.querySelectorAll('.qc-row .qc-name')].map(node=>node.textContent);
 assert.deepEqual(names,['WeatherNext 3','ECMWF-AIFS','ECMWF-IFS','NCEP-GFS','深圳格点实况','WeatherNext 2']);
 assert.deepEqual([...host.querySelectorAll('.qc-row')].map(row=>row.className.replace('qc-row ','')),['bad','warn','warn','good','good','neutral']);

 // 一句话结论只数已接入的资料源，数字和下面各行对得上。
 const verdict=host.querySelector('.qc-verdict');
 assert.ok(verdict.classList.contains('bad'));
 assert.match(verdict.textContent,/5 个资料源中有 3 个需要关注/);
 assert.match(verdict.textContent,/1 个异常 · 2 个需关注 · 2 个正常 · 另有 1 个未接入/);

 const row=id=>host.querySelector(`details[data-key="source:${id}"]`);
 // 缺的时效写具体，不重复“存在缺失时效”那句；重试原因照写。
 const ec=row('ecmwf').querySelector('.qc-reason').textContent;
 assert.match(ec,/^63\/65 个预报时间；2 个时效未入库，等待下载程序重试：\+36h、\+120h（累计降水比前一时效小）$/);
 assert.ok(!ec.includes('已发布时效范围内存在缺失'));
 assert.equal(row('ecmwf').querySelector('summary .qc-tag').textContent,'部分到报');
 // 到报正常但有待重试项：整行标需关注。
 assert.equal(row('aifs').querySelector('summary .qc-tag').textContent,'需关注');
 assert.match(row('gfs').querySelector('.qc-reason').textContent,/81\/81 个预报时间，已到齐/);
 assert.match(row('observations').querySelector('.qc-reason').textContent,/最近 24 小时已入库 23 小时；1 个小时还在正常等待时间内/);
 assert.match(row('observations').querySelector('.qc-latest').textContent,/11-10 19:00 观测/);
 assert.match(row('gfs').querySelector('.qc-latest').textContent,/11-10 08:00 起报.*12\.9 小时前/);
 assert.equal(row('weathernext2').querySelector('summary .qc-tag').textContent,'未接入');
 assert.equal(row('weathernext2').querySelector('.qc-week'),null);

 // 近 7 天：没记录 / 都到齐 / 有没到齐 / 最新一批还在下载不算没到齐。
 assert.deepEqual([...row('gfs').querySelectorAll('summary .qc-day')].map(cell=>cell.className.replace('qc-day ','')),['none','none','none','none','full','miss','full']);
 assert.match(row('gfs').querySelector('.qc-day.miss').title,/11-09：记录到 4 批，到齐 3 批，1 批没到齐/);
 assert.match(row('gfs').querySelectorAll('summary .qc-day')[6].title,/最新的还在下载/);
 assert.match(row('ecmwf').querySelector('.qc-week-cell').textContent,/暂无记录/);
 // 展开后：问题清单 + 最近几批。
 assert.match(row('gfs').querySelector('.qc-row-body').textContent,/本次检查没有发现问题/);
 assert.match(row('gfs').querySelector('.qc-row-body table').textContent,/11-10 08:00还在下载.*11-10 02:0011-10 07:305\.5 小时81 \/ 81/);
 assert.equal(row('ecmwf').querySelectorAll('.qc-issues li').length,2);

 // 旧版的三个筛选和四张统计卡不再出现。
 assert.equal(host.querySelectorAll('select,.qc-stat,[data-quality-severity]').length,0);
 assert.match(host.querySelector('.qc-rules').textContent,/演示模式不重复统计/);
 assert.deepEqual(calls,[['GET','/Verification/test/api/quality-control/catalog']]);

 // 重新检查后，展开着的行保持展开，新结果生效。
 row('ecmwf').open=true;
 next=payload();next.snapshot.sources=next.snapshot.sources.filter(item=>item.id!=='weathernext3');
 host.querySelector('#quality-refresh').click();
 await until(()=>host.querySelectorAll('.qc-row').length===5);
 assert.ok(row('ecmwf').open);assert.ok(!row('gfs').open);
 assert.equal(calls[1][0],'POST');
 assert.equal(host.querySelector('.qc-verdict').textContent,'4 个资料源中有 2 个需要关注2 个正常 · 另有 1 个未接入');
 // 缺的时效没有别的说明时，仍然单独写出来。
 next=payload();next.snapshot.sources=next.snapshot.sources.filter(item=>item.id!=='weathernext3');
 next.snapshot.sources.find(item=>item.id==='ecmwf').issues.pop();
 next.snapshot.sources.find(item=>item.id==='ecmwf').arrival.missing_leads=[3,36];
 host.querySelector('#quality-refresh').click();
 await until(()=>/缺 \+3h/.test(row('ecmwf').querySelector('.qc-reason').textContent));
 assert.equal(row('ecmwf').querySelector('.qc-reason').textContent,'63/65 个预报时间；缺 +3h、+36h');
 assert.ok(host.querySelector('.qc-verdict').classList.contains('warn'));

 // 全部正常时只说一句。
 next=payload();next.snapshot.sources=next.snapshot.sources.filter(item=>['gfs','observations'].includes(item.id));
 host.querySelector('#quality-refresh').click();
 await until(()=>host.querySelectorAll('.qc-row').length===2);
 assert.equal(host.querySelector('.qc-verdict strong').textContent,'2 个资料源全部正常');
 assert.equal(host.querySelector('.qc-verdict').textContent,'2 个资料源全部正常');

 window.DataQuality.close();
 console.log('quality frontend ok');
})().catch(failure=>{console.error(failure);process.exit(1);});
