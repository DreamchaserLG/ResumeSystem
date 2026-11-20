<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowBackOutline, DocumentTextOutline } from '@vicons/ionicons5'
import { ElButton, ElPagination, ElProgress, ElSelect, ElOption, ElTag, ElTooltip } from 'element-plus'
import { api } from '@/api/client'
import { jobStatusLabel, label, schoolTagLabels, type JobCandidatePage, type ResumeCard } from '@/api/hr'

type CandidateRow = JobCandidatePage['items'][number]
type MatchDimension = { key:string; label:string; score:number; weight:number; color:string; pale:string; evidence:string[] }
const route=useRoute()
const router=useRouter()
const data=ref<JobCandidatePage>()
const page=ref(1)
const size=ref(20)
const loading=ref(false)
const error=ref('')
const selectedId=ref<number>()
const reportVisible=ref(false)
const jobId=computed(()=>String(route.params.jobId || ''))
/* 头像按行加载：解析失败或无头像时回退为名字。 */
const avatars=ref(new Map<number,string>())
let avatarUrls:string[]=[]
function releaseAvatars() {for(const url of avatarUrls) URL.revokeObjectURL(url);avatarUrls=[];avatars.value=new Map()}
async function loadAvatars(items:CandidateRow[]) {
  const results=await Promise.allSettled(items.filter(item=>item.has_avatar && item.version_id)
    .map(async item=>[item.id,await api.get<Blob>('/hr/applications/'+item.id+'/avatar?version='+item.version_id)] as const))
  const next=new Map<number,string>()
  for(const result of results) if(result.status==='fulfilled') next.set(result.value[0],URL.createObjectURL(result.value[1]))
  avatars.value=next;avatarUrls=[...next.values()]
}
let requestSequence=0
const demo=ref(false)
async function load() {
  const seq=++requestSequence
  error.value=''; loading.value=true
  try {
    const result=await api.get<JobCandidatePage>('/jobs/'+encodeURIComponent(jobId.value)+'/candidates?'+new URLSearchParams({page:String(page.value),size:String(size.value)}))
    if(seq!==requestSequence) return
    demo.value=false; data.value=result
    selectedId.value=undefined; releaseAvatars()
    void loadAvatars(result.items)
  } catch { if(seq===requestSequence){error.value='';demo.value=true;data.value=buildMock(jobId.value,page.value,size.value);releaseAvatars()} }
  finally {if(seq===requestSequence)loading.value=false}
}
watch([jobId,page,size],([id],previous)=>{
  if(id!==previous?.[0]) {data.value=undefined;if(page.value!==1){page.value=1;return}}
  void load()
},{immediate:true})
const selected=computed<CandidateRow|undefined>(()=>data.value?.items.find(item=>item.id===selectedId.value))
function degreeLine(tags:ResumeCard['tags']) {
  const degree=tags.find(tag=>tag.type==='degree')
  const tiers=tags.filter(tag=>tag.type==='school' && schoolTagLabels[tag.value]).map(tag=>schoolTagLabels[tag.value])
  const degreeText=degree?label(degree.value):''
  return tiers.length ? (degreeText?degreeText+'（'+tiers.join('/')+'）':tiers.join('/')) : degreeText
}
function experienceText(tags:ResumeCard['tags']) {
  const tag=tags.find(tag=>tag.type==='experience')
  const months=tag?Number(tag.value):NaN
  if(!Number.isFinite(months) || months<0) return ''
  if(months===0) return '应届生'
  if(months<12) return '1 年以内'
  return Math.floor(months/12)+' 年工作经验'
}
function selectCandidate(row:CandidateRow) { selectedId.value=row.id;reportVisible.value=true }
function matchColor(percent:number) {return percent>=80?'#3a9671':percent>=60?'#c99a2e':percent>=40?'#d97f3e':'#c0504d'}
function toggleReport() {
  if(!reportVisible.value && !selected.value) selectedId.value=data.value?.items[0]?.id
  reportVisible.value=!reportVisible.value
}
function openProfile(id:number) { void router.push({name:'applications',query:{job:String(jobId.value),candidate:String(id)}}) }

/* —— 演示数据：后端未接入或岗位无数据时，用确定性伪随机生成版式预览内容 —— */
const surnamePool='王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗郑梁谢宋唐许韩冯邓曹彭'.split('')
const givenPool=['泽宇','思远','嘉树','一鸣','欣然','书航','孟杰','云帆','晓彤','语桐','铭轩','浩然','子墨','雨泽','若曦','书瑶','志强','紫萱','明哲','俊驰']
const companyPool=['字节跳动','腾讯','阿里云','美团','快手','京东','网易','华为','百度','小米','拼多多','微软中国','商汤科技','科大讯飞']
const cityPool=['北京','上海','深圳','杭州','广州','成都','南京','武汉','西安','苏州']
const notePool=['已电话初筛，候选人意向明确','等待技术面安排','简历两段经历重叠，需核实','薪资期望偏高，暂挂起','推荐进入下一轮','联系方式已确认，约下周面试','用人部门更看重项目经验，待评估','两轮面试均已通过，等待终面','近期跳槽较频繁，风险待评估','候选人在职，考虑机会中']
const degreePool=['bachelor','bachelor','master','master','phd','associate']
const schoolPool:Record<string,string[]>={is_985:['清华大学','北京大学','浙江大学','复旦大学'],is_211:['北京邮电大学','西安电子科技大学','南京理工大学','河海大学'],is_double_first_class:['南方科技大学','宁波大学'],study_abroad:['帝国理工学院','墨尔本大学'],qs_top100:['香港大学','多伦多大学']}
const skillPool=['Java','Spring Boot','Kubernetes','Kafka','Redis','MySQL','分布式系统','微服务架构','Docker','CI/CD','PostgreSQL','Elasticsearch']
const rolePool=['高级后端开发工程师','资深 Java 工程师','平台架构师','数据平台工程师','算法工程师']
function buildMock(jobKey:string,pageNum:number,pageSize:number):JobCandidatePage {
  let seed=7
  for(const ch of jobKey) seed=(seed*31+ch.charCodeAt(0))%2147483647
  const rand=()=>{seed=seed*16807%2147483647;return (seed-1)/2147483646}
  const pick=<T,>(pool:T[]):T=>pool[Math.floor(rand()*pool.length)]
  const total=24+Math.floor(rand()*26) /* 每个岗位 24–49 条，不超过 50 */
  const skills=[...new Set(Array.from({length:5},()=>pick(skillPool)))]
  const job={id:Number(jobKey)||0,title:pick(rolePool)+'（演示）',status:'open',department_name:'技术研发部',
    requirements:'负责核心交易链路的设计与演进，主导高并发场景下的稳定性建设，参与团队技术方案评审与新人培养。',
    required_skills:skills,experience_tags:['5–10年'],recruitment_start:'2026-11-01',recruitment_end:'2026-12-31',target_date:'2026-12-31'}
  const items:JobCandidatePage['items']=Array.from({length:total},(_,index)=>{
    const tags:ResumeCard['tags']=[{id:'degree-'+index,type:'degree',value:pick(degreePool),filterable:true}]
    if(rand()<=.85) tags.push({id:'school-'+index,type:'school',value:pick(Object.keys(schoolPool)),filterable:true})
    tags.push({id:'exp-'+index,type:'experience',value:String(index%4===0?0:48+Math.floor(rand()*144)),filterable:true})
    return {id:(Number(jobKey)||index+1)*1000+index+1,version_id:null,job_id:job.id,job_title:job.title,name:pick(surnamePool)+pick(givenPool),
      candidate_id:(Number(jobKey)||index+1)*1000+index+1,in_talent:rand()>.6,talent_status:rand()>.6?pick(['interviewing','hired','rejected']):null,
      target_role:pick(rolePool),gender:null,city:pick(cityPool),address:null,phone:null,status:'active',
      stage:{code:'review' as const,label:'待筛选',round_id:null},workflow_version:1,parse_status:'published',
      page_count:1+Math.floor(rand()*2),has_avatar:false,tags,latest_note:rand()>.28?pick(notePool):null,
      salary_midpoint:null,recent_company:null,current_company:pick(companyPool),match_percent:42+Math.floor(rand()*56)}
  }).sort((a,b)=>(b.match_percent??0)-(a.match_percent??0))
  return {job,items:items.slice((pageNum-1)*pageSize,pageNum*pageSize),total,page:pageNum,size:pageSize,school_filters:false,matched_requirements:skills.length}
}

/* 当前仅用于报告版式预览；算法接入后替换为服务端的可解释评分结果。 */
const dimensions:MatchDimension[]=[
  {key:'skill',label:'技能',score:85,weight:.40,color:'#4F81BD',pale:'#DCE6F1',evidence:['Java / Spring Boot','Kafka、Redis 等中间件']},
  {key:'project',label:'项目',score:72,weight:.25,color:'#9BBB59',pale:'#E2EFD9',evidence:['主导微服务迁移项目','高并发订单场景']},
  {key:'capability',label:'能力',score:68,weight:.20,color:'#F79646',pale:'#FDE9D9',evidence:['架构设计与跨团队协作','技术方案评审']},
  {key:'experience',label:'经历',score:60,weight:.15,color:'#8064A2',pale:'#E4DFEC',evidence:['5 年后端研发经验']},
]
const finalScore=50.4, baseScore=74.6, penaltyFactor=.675, penaltyLoss=.325
const penaltyLossAmount=(baseScore*(1-penaltyFactor)).toFixed(1)
const hoverPenalty=ref<string>()
const penalties=[
  {label:'工作年限',detail:'要求 ≥6 年 / 演示值 5 年',severity:'轻微不足',factor:.90,linked:'experience'},
  {label:'必备技能 Kubernetes',detail:'未命中',severity:'中度不足',factor:.75,linked:'skill'},
]
function point(radius:number,angle:number) {const radians=(angle-90)*Math.PI/180;return {x:120+radius*Math.cos(radians),y:120+radius*Math.sin(radians)}}
function arc(radius:number,start:number,end:number) {const first=point(radius,start),last=point(radius,end);return `M ${first.x.toFixed(2)} ${first.y.toFixed(2)} A ${radius} ${radius} 0 ${end-start>180?1:0} 1 ${last.x.toFixed(2)} ${last.y.toFixed(2)}`}
const chartDimensions=computed(()=>{let start=0;return dimensions.map(item=>{const span=item.weight*360;const result={...item,start,end:start+span,fillEnd:start+span*item.score/100,labelPosition:point(102,start+span/2)};start+=span;return result})})
const finalColor=computed(()=>finalScore>=80?'#4CAF50':finalScore>=60?'#FFC107':finalScore>=40?'#FF9800':'#F44336')
const profileName=computed(()=>selected.value?.name || '申请 #'+(selected.value?.id ?? '—'))
onBeforeUnmount(releaseAvatars)
</script>

<template>
  <main class="hire-page job-candidates-page">
    <header class="hire-heading job-candidates-heading">
      <div><h1>{{ data?.job.title || '岗位候选人' }}</h1><p v-if="data">{{ data.job.department_name }} · 关联候选人</p></div>
      <div class="job-heading-actions">
        <ElTag v-if="data" :type="data.job.status==='open'?'success':data.job.status==='paused'?'warning':'info'" effect="light">{{ jobStatusLabel(data.job.status) }}</ElTag>
        <ElTooltip content="返回岗位管理" placement="bottom"><ElButton circle aria-label="返回岗位管理" @click="router.push({name:'jobs'})"><ArrowBackOutline aria-hidden="true"/></ElButton></ElTooltip>
      </div>
    </header>
    <p v-if="error" class="hire-error" role="alert">{{ error }} <ElButton text type="primary" @click="load">重试</ElButton></p>
    <p v-else-if="!data" class="hire-muted" role="status">正在加载候选人…</p>
    <template v-if="data">
      <section class="hire-surface candidate-job-summary" aria-label="岗位要求概览">
        <div><span>招聘期限</span><strong>{{ data.job.recruitment_start || '未设置' }} 至 {{ data.job.recruitment_end || '未设置' }}</strong></div>
        <div><span>必备技能</span><div class="job-tags"><ElTag v-for="tag in data.job.required_skills" :key="tag" effect="plain" size="small">{{ tag }}</ElTag><strong v-if="!data.job.required_skills.length">未设置</strong></div></div>
        <div><span>工作经验要求</span><div class="job-tags"><ElTag v-for="tag in data.job.experience_tags" :key="tag" effect="plain" size="small">{{ tag }}</ElTag><strong v-if="!data.job.experience_tags.length">未设置</strong></div></div>
      </section>
      <section class="candidate-workbench" :class="{ 'report-collapsed':!reportVisible }" :aria-busy="loading">
        <section class="hire-surface candidate-job-list">
          <header class="candidates-title">
            <div><h2>关联简历 <ElTag v-if="demo" size="small" type="warning" effect="plain">演示数据</ElTag> <span>{{ data.total }} 份</span></h2><p v-if="data.matched_requirements">根据已识别的 {{ data.matched_requirements }} 项岗位要求排序；点击候选人查看匹配报告。</p><p v-else>点击候选人查看右侧匹配报告。</p></div>
            <div class="candidate-list-actions">
              <ElTooltip content="打开候选人工作台" placement="bottom"><ElButton circle aria-label="打开候选人工作台" :disabled="!selected" @click="selected && openProfile(selected.id)"><DocumentTextOutline aria-hidden="true"/></ElButton></ElTooltip>
              <button type="button" class="report-toggle" :aria-pressed="reportVisible"
                :title="reportVisible?'收起匹配报告':'展开匹配报告'" :aria-label="reportVisible?'收起匹配报告':'展开匹配报告'" @click="toggleReport">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true" focusable="false">
                  <rect x="3" y="4.5" width="18" height="15" rx="3"/>
                  <path v-if="!reportVisible" d="M9.5 4.5v15"/>
                  <path v-else d="M14.5 4.5v15"/>
                </svg>
              </button>
            </div>
          </header>
          <div class="candidate-grid-head" aria-hidden="true"><span>候选人</span><span>匹配度</span></div>
          <div v-if="data.items.length" class="candidate-rows" role="list" aria-label="关联候选人列表">
            <article v-for="row in data.items" :key="row.id" class="candidate-row" :class="{selected:row.id===selected?.id}" role="listitem" tabindex="0" @click="selectCandidate(row)" @keydown.enter="selectCandidate(row)">
              <div class="candidate-person">
                <span class="candidate-avatar" aria-hidden="true"><img v-if="avatars.get(row.id)" :src="avatars.get(row.id)" alt=""><i v-else>{{ (row.name || '申').slice(0,2) }}</i></span>
                <div class="candidate-person-info">
                  <div class="candidate-name-line">
                    <button type="button" class="candidate-name" @click.stop="openProfile(row.id)">{{ row.name || '申请 #'+row.id }}</button>
                    <span v-if="degreeLine(row.tags)" class="degree-inline">{{ degreeLine(row.tags) }}</span>
                  </div>
                  <small>{{ [row.target_role || row.job_title, experienceText(row.tags)].filter(Boolean).join(' · ') || '—' }}</small>
                </div>
              </div>
              <div class="candidate-progress"><ElProgress v-if="row.match_percent!=null" :percentage="row.match_percent" :stroke-width="8" :show-text="false" :color="matchColor(row.match_percent)"/><span v-else class="progress-empty">—</span></div>
            </article>
          </div>
          <p v-else class="hire-empty">{{ loading?'正在加载…':'该岗位暂未关联简历' }}</p>
          <footer class="candidate-job-pagination"><span>每页</span><ElSelect v-model="size" size="small" aria-label="每页候选人数"><ElOption v-for="option in [10,20,30,50]" :key="option" :label="option+' 条'" :value="option"/></ElSelect><ElPagination v-model:current-page="page" :page-size="size" :total="data.total" :pager-count="5" layout="total, prev, pager, next"/></footer>
        </section>
        <aside v-if="reportVisible && selected" class="hire-surface explain-report" aria-label="可解释匹配报告">
          <header class="report-title">
            <div><span class="report-eyebrow">可解释匹配报告</span><h2>{{ profileName }}</h2></div>
            <span class="report-status"><i></i>报告预览</span>
          </header>
          <div class="report-scroll">
            <p class="report-disclaimer">用岗位要求逐项核对简历证据，分数越高表示当前证据越充分。</p>
            <section class="report-overview" aria-label="综合匹配分">
              <div class="match-chart-wrap"><svg viewBox="0 0 240 240" class="match-chart" role="img" aria-label="四维匹配度与最终匹配度示意图"><path v-for="dimension in chartDimensions" :key="dimension.key+'-base'" :class="{dimmed:hoverPenalty && hoverPenalty!==dimension.key}" :d="arc(83,dimension.start+1,dimension.end-1)" fill="none" :stroke="dimension.pale" stroke-width="20"/><path v-for="dimension in chartDimensions" :key="dimension.key+'-fill'" :class="{dimmed:hoverPenalty && hoverPenalty!==dimension.key}" :d="arc(83,dimension.start+1,Math.max(dimension.start+1.01,dimension.fillEnd-1))" fill="none" :stroke="dimension.color" stroke-width="20"/><path :d="arc(104,0,360*penaltyLoss)" fill="none" stroke="#C0504D" stroke-width="3.5" stroke-linecap="round"/><circle cx="120" cy="120" r="56" fill="white" stroke="#eee" stroke-width="10"/><path :d="arc(56,0,360*finalScore/100)" fill="none" :stroke="finalColor" stroke-width="10" stroke-linecap="round"/><g v-for="dimension in chartDimensions" :key="dimension.key+'-label'" class="chart-label"><text :x="dimension.labelPosition.x" :y="dimension.labelPosition.y-4" text-anchor="middle">{{ dimension.label }}</text><text :x="dimension.labelPosition.x" :y="dimension.labelPosition.y+11" text-anchor="middle" class="chart-score">{{ dimension.score }}</text></g><text x="120" y="112" class="chart-final" text-anchor="middle" :fill="finalColor">{{ finalScore }}</text><text x="120" y="129" class="chart-base" text-anchor="middle">最终匹配分</text><text x="120" y="142" class="chart-penalty" text-anchor="middle">基础 {{ baseScore }} · 扣 {{ penaltyLossAmount }}</text></svg></div>
              <div class="score-summary"><span>岗位基础分</span><strong>{{ baseScore }}</strong><small>硬性条件系数 ×{{ penaltyFactor }}</small></div>
            </section>
            <section class="dimension-list" aria-label="分维度证据">
              <div class="report-section-heading"><h3>分项证据</h3><span>共 {{ dimensions.length }} 项</span></div>
              <article v-for="dimension in dimensions" :key="dimension.key" class="dimension-row" :class="{linked:hoverPenalty===dimension.key}" :style="{'--dimension-color':dimension.color,'--dimension-pale':dimension.pale}">
                <div class="dimension-heading"><span class="dimension-label"><i class="dimension-dot"></i>{{ dimension.label }}</span><strong>{{ dimension.score }}<small>/100</small></strong></div>
                <div class="dimension-progress" role="progressbar" :aria-label="dimension.label+'匹配分'" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="dimension.score"><i :style="{width:dimension.score+'%'}"></i></div>
                <p>权重 {{ Math.round(dimension.weight*100) }}%</p><small>{{ dimension.evidence.join(' · ') }}</small>
              </article>
            </section>
            <section class="penalty-list" aria-label="硬性条件核对"><div class="report-section-heading"><h3>硬性条件核对</h3><span>{{ penalties.length }} 项确认</span></div><p v-for="item in penalties" :key="item.label" @mouseenter="hoverPenalty=item.linked" @mouseleave="hoverPenalty=undefined"><span>×</span><span><strong>{{ item.label }}</strong>{{ item.detail }}</span><b>{{ item.severity }} · ×{{ item.factor }}</b></p></section>
          </div>
        </aside>
      </section>
    </template>
  </main>
</template>

<style scoped>
.job-candidates-heading{margin-bottom:9px}.job-heading-actions,.candidate-list-actions{display:flex;align-items:center;gap:8px}.job-heading-actions :deep(.el-button),.candidate-list-actions :deep(.el-button){width:31px;height:31px;padding:0}.job-heading-actions :deep(svg),.candidate-list-actions :deep(svg){width:16px;height:16px}.report-toggle{display:inline-flex;align-items:center;justify-content:center;width:31px;height:31px;padding:0;border:1px solid var(--hire-line);border-radius:50%;background:var(--hire-paper);color:#54707f;cursor:pointer;transition:border-color .14s ease,background-color .14s ease,color .14s ease}.report-toggle svg{width:16px;height:16px}.report-toggle:hover,.report-toggle[aria-pressed=true]{border-color:var(--hire-accent);background:var(--hire-soft);color:var(--hire-accent-dark)}.candidate-job-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px;padding:15px 17px;margin-bottom:14px}.candidate-job-summary>div{display:grid;gap:6px;align-content:start}.candidate-job-summary span{font-size:12px;color:var(--hire-muted)}.candidate-job-summary strong{font-size:13px}.job-tags{display:flex;gap:5px;flex-wrap:wrap}.candidate-workbench{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,375px);gap:14px;align-items:start}.candidate-job-list{overflow:hidden}.candidates-title{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:14px 17px;border-bottom:1px solid var(--hire-line)}.candidates-title h2{font-size:16px}.candidates-title h2 span:not(.el-tag),.candidates-title p{color:var(--hire-muted);font-size:12px;font-weight:400}.candidates-title p{margin-top:5px;line-height:1.45}.candidate-grid-head,.candidate-row{display:grid;grid-template-columns:minmax(210px,1.45fr) minmax(170px,1fr) minmax(112px,.62fr);gap:13px;align-items:center}.candidate-grid-head{padding:9px 17px;background:#f7f9fa;color:var(--hire-muted);font-size:12px}.candidate-row{width:100%;padding:12px 17px;border:0;border-bottom:1px solid #edf1f3;background:white;text-align:left;cursor:pointer;transition:background-color .14s ease,box-shadow .14s ease}.candidate-row:hover,.candidate-row:focus-visible{background:#f6fbfa;outline:none}.candidate-row.selected{background:#eff8f6;box-shadow:inset 3px 0 var(--hire-accent)}.candidate-person{display:flex;gap:10px;align-items:center;min-width:0}.candidate-avatar{display:grid;place-items:center;width:38px;height:38px;flex:none;border-radius:50%;overflow:hidden;background:linear-gradient(135deg,var(--hire-soft),#eef0ff);border:1px solid #d3e7e3;color:var(--hire-accent-dark);font-size:13px;font-weight:650}.candidate-avatar img{width:100%;height:100%;object-fit:cover}.candidate-person-info{min-width:0}.candidate-name-line{display:flex;align-items:baseline;gap:7px;min-width:0;flex-wrap:wrap}.candidate-name{border:0;padding:0;background:none;color:var(--hire-ink);font:650 13px/1.35 inherit;cursor:pointer;text-decoration:none}.candidate-name:hover{color:var(--hire-accent-dark)}.degree-inline{padding:2px 8px;border:1px solid #d5e7e2;border-radius:20px;background:#f0f7f5;color:var(--hire-accent-dark);font-size:11px;font-weight:600;white-space:nowrap}.candidate-person-info small{display:block;color:var(--hire-muted);font-size:11px;margin-top:3px;line-height:1.3}.candidate-followup{font-size:12px;line-height:1.5;overflow-wrap:anywhere;color:#3d5158;display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.candidate-followup.is-empty{color:var(--hire-muted)}.candidate-progress{min-width:0}.candidate-progress :deep(.el-progress-bar__outer){background:#e7efed}.progress-empty{color:#9aabaa}.candidate-job-pagination{display:flex;align-items:center;justify-content:flex-end;gap:7px;padding:11px 15px;font-size:12px;color:var(--hire-muted)}.candidate-job-pagination :deep(.el-select){width:76px}.explain-report{padding:15px;position:sticky;top:12px}.report-title{display:flex;align-items:start;justify-content:space-between;gap:8px}.report-eyebrow{display:block;color:var(--hire-accent-dark);font-size:11px;font-weight:650;letter-spacing:.06em}.report-title h2{font-size:16px;margin-top:2px}.report-disclaimer{margin-top:6px;color:var(--hire-muted);font-size:11px;line-height:1.45}.match-chart-wrap{display:grid;place-items:center;margin:2px auto -2px}.match-chart{width:min(100%,248px);overflow:visible}.match-chart path{transition:opacity .15s ease}.match-chart path.dimmed{opacity:.3}.chart-label{font-size:10px;fill:#465767;font-weight:650}.chart-score{font-size:12px;fill:#202c3b}.chart-final{font-size:25px;font-weight:720}.chart-base{font-size:10px;fill:#677585}.chart-penalty{font-size:9px;fill:#C0504D;font-weight:650}.dimension-list{display:grid;gap:5px;margin-top:10px}.dimension-row{display:flex;gap:8px;align-items:flex-start;padding:7px 8px;border:1px solid #edf0f2;border-radius:7px;transition:border-color .15s ease,background-color .15s ease}.dimension-row.linked{border-color:var(--dimension-color);background:var(--dimension-pale)}.dimension-dot{display:block;width:7px;height:7px;flex:none;border-radius:50%;background:var(--dimension-color);margin-top:5px}.dimension-row strong{display:block;font-size:12px}.dimension-row small{display:block;margin-top:2px;font-size:11px;line-height:1.35;color:var(--hire-muted)}.penalty-list{border-top:1px solid var(--hire-line);margin-top:12px;padding-top:10px}.penalty-list h3{font-size:12px;margin-bottom:5px}.penalty-list p{color:#885450;font-size:11px;line-height:1.55;cursor:default}.penalty-list b{color:#bd534f;font-weight:650}@media(max-width:1120px){.candidate-workbench,.candidate-workbench.report-collapsed{grid-template-columns:1fr}.explain-report{position:static;display:grid;grid-template-columns:minmax(220px,.9fr) minmax(260px,1.1fr);column-gap:14px}.report-title,.report-disclaimer{grid-column:1/-1}.match-chart-wrap{grid-column:1}.dimension-list{grid-column:2;grid-row:3/4;align-self:center}.penalty-list{grid-column:1/-1}}@media(max-width:760px){.candidate-job-summary{grid-template-columns:1fr;gap:11px}.candidate-grid-head{display:none}.candidate-row{grid-template-columns:1fr;gap:8px}.candidate-progress{grid-column:1/-1}.candidate-job-pagination{justify-content:flex-start;flex-wrap:wrap}.explain-report{display:block}.dimension-list{margin-top:10px}}
</style>
<style scoped>
.job-candidates-page { width:100%;max-width:none; }
.candidate-job-summary {
  border-color:#d9e7e7;
  background:linear-gradient(135deg,rgba(255,255,255,.98),rgba(246,251,250,.98));
}
.candidate-job-summary > div {
  position:relative;
  padding-inline-start:11px;
}
.candidate-job-summary > div::before {
  content:'';
  position:absolute;
  inset:2px auto 2px 0;
  width:3px;
  border-radius:3px;
  background:linear-gradient(180deg,var(--hire-accent),#6b7fdd);
  opacity:.58;
}
.candidate-row { transition:background-color var(--rs-motion-fast),box-shadow var(--rs-motion-fast),transform var(--rs-motion-fast) var(--rs-ease); }
.candidate-row:hover,.candidate-row:focus-visible { background:#f2faf8;transform:translateX(2px); }
.candidate-row.selected { background:linear-gradient(90deg,#e9f8f4,#f8fcfb);box-shadow:inset 3px 0 var(--hire-accent),inset 12px 0 24px rgba(13,137,126,.035); }
.candidate-workbench { grid-template-columns:minmax(680px,1fr) clamp(440px,31vw,530px);gap:18px; }
.candidate-workbench.report-collapsed { grid-template-columns:minmax(0,1fr); }
.candidate-grid-head,.candidate-row {grid-template-columns:minmax(0,1fr) minmax(84px,.38fr)}
.candidate-job-list { min-height:calc(100dvh - var(--rs-header-height) - 190px); }
.explain-report {
  display:flex;flex-direction:column;height:calc(100dvh - var(--rs-header-height) - 24px);min-height:min(620px,calc(100dvh - var(--rs-header-height) - 24px));max-height:calc(100dvh - var(--rs-header-height) - 24px);
  padding:0;overflow:hidden;border-color:#d6e7e4;box-shadow:0 14px 38px rgba(18,50,61,.09);
}
.report-title { flex:none;padding:17px 18px 14px;border-bottom:1px solid #e4ecec;background:linear-gradient(180deg,#fff,#fbfdfd); }
.report-title h2 { max-width:290px;margin-top:3px;overflow:hidden;color:#18343c;font-size:17px;text-overflow:ellipsis;white-space:nowrap; }
.report-eyebrow { color:#157f75;font-size:10px;letter-spacing:.1em;text-transform:uppercase; }
.report-status { display:inline-flex;align-items:center;gap:6px;padding:5px 9px;border:1px solid #d7e7e4;border-radius:999px;background:#f3faf8;color:#4d6b69;font-size:11px; }
.report-status i { width:6px;height:6px;border-radius:50%;background:#22a696;box-shadow:0 0 0 3px rgba(34,166,150,.12); }
.report-scroll { flex:1;min-height:0;overflow:auto;padding:14px 16px 18px;scrollbar-width:thin;scrollbar-color:#b7cbc8 transparent; }
.report-disclaimer { margin:0 0 12px;padding:9px 11px;border:1px solid #e1ecea;border-radius:9px;background:#f6faf9;color:#5f7479;font-size:11px;line-height:1.5; }
.report-overview { position:relative;display:grid;grid-template-columns:minmax(0,1fr) 124px;align-items:center;min-height:250px;padding:5px 10px 5px 4px;overflow:hidden;border:1px solid #e0e9e9;border-radius:14px;background:radial-gradient(circle at 30% 38%,#fff 0,#f7fbfa 56%,#f1f7f6 100%); }
.report-overview::after { content:'';position:absolute;right:-50px;bottom:-70px;width:170px;height:170px;border:1px solid rgba(29,149,137,.12);border-radius:50%;box-shadow:0 0 0 24px rgba(29,149,137,.025);pointer-events:none; }
.match-chart-wrap { margin:0; }
.match-chart { width:min(100%,270px);filter:drop-shadow(0 8px 16px rgba(25,66,71,.055)); }
.score-summary { position:relative;z-index:1;display:grid;align-content:center;gap:2px;padding:15px 13px;border:1px solid rgba(208,226,223,.95);border-radius:11px;background:rgba(255,255,255,.88);box-shadow:0 7px 20px rgba(25,65,70,.055); }
.score-summary span,.score-summary small { color:var(--hire-muted);font-size:10px;line-height:1.4; }
.score-summary strong { color:#1b4f4a;font-size:25px;line-height:1.15;font-variant-numeric:tabular-nums; }
.report-section-heading { display:flex;align-items:center;justify-content:space-between;gap:12px;margin:2px 1px 7px; }
.report-section-heading h3 { color:#294049;font-size:12px; }
.report-section-heading span { color:var(--hire-muted);font-size:10px; }
.dimension-list { gap:8px;margin-top:14px; }
.dimension-row { display:block;padding:10px 11px 11px;border:1px solid #e5ecec;border-radius:11px;background:#fff;transition:border-color var(--rs-motion-fast),background-color var(--rs-motion-fast),box-shadow var(--rs-motion-fast); }
.dimension-row:hover,.dimension-row.linked { border-color:var(--dimension-color);background:linear-gradient(135deg,#fff,var(--dimension-pale));box-shadow:0 5px 14px rgba(28,65,72,.055);transform:none; }
.dimension-heading { display:flex;align-items:center;justify-content:space-between;gap:10px; }
.dimension-label { display:inline-flex;align-items:center;gap:7px;color:#2a414a;font-size:12px;font-weight:680; }
.dimension-dot { width:7px;height:7px;margin:0;border-radius:50%;background:var(--dimension-color);box-shadow:0 0 0 3px var(--dimension-pale); }
.dimension-heading strong { color:var(--dimension-color);font-size:15px;font-variant-numeric:tabular-nums; }
.dimension-heading strong small { display:inline;margin:0 0 0 2px;color:#8b9a9f;font-size:9px;font-weight:500; }
.dimension-progress { height:5px;margin:8px 0 7px;overflow:hidden;border-radius:999px;background:var(--dimension-pale); }
.dimension-progress i { display:block;height:100%;border-radius:inherit;background:var(--dimension-color);box-shadow:0 0 8px var(--dimension-color);transition:width .45s var(--rs-ease); }
.dimension-row>p { margin:0 0 2px;color:#829095;font-size:10px; }
.dimension-row>small { display:block;margin:0;color:#556a71;font-size:11px;line-height:1.45; }
.penalty-list { margin-top:14px;padding-top:12px; }
.penalty-list>p { display:grid;grid-template-columns:17px minmax(0,1fr) auto;align-items:start;gap:7px;margin-top:6px;padding:9px 10px;border:1px solid #f0dedd;border-radius:9px;background:#fff9f8;color:#765a58;line-height:1.4; }
.penalty-list>p>span:first-child { display:grid;place-items:center;width:17px;height:17px;border-radius:50%;background:#f9e4e1;color:#bc534e;font-weight:750; }
.penalty-list>p>span:nth-child(2) { display:grid;gap:2px;font-size:10px; }
.penalty-list>p strong { color:#714c49;font-size:11px; }
.penalty-list b { align-self:center;padding:3px 6px;border-radius:5px;background:#f9e8e5;color:#a94d49;font-size:9px;white-space:nowrap; }
@media(max-width:1460px) {
  .candidate-workbench,.candidate-workbench.report-collapsed { grid-template-columns:1fr; }
  .explain-report { position:static;height:auto;min-height:0;max-height:none; }
  .report-scroll { display:grid;grid-template-columns:minmax(380px,.9fr) minmax(420px,1.1fr);gap:14px 18px;overflow:visible; }
  .report-disclaimer { grid-column:1/-1;margin:0; }
  .report-overview { grid-column:1;grid-row:2/4; }
  .dimension-list { grid-column:2;grid-row:2;margin-top:0; }
  .penalty-list { grid-column:2;grid-row:3;margin-top:0;padding-top:10px; }
}
@media(max-width:930px) {
  .report-scroll { display:block; }
  .report-overview { margin-top:12px; }
}
@media(max-width:760px) {
  .candidate-workbench,.candidate-workbench.report-collapsed { display:block; }
  .explain-report { margin-top:14px; }
  .report-overview { grid-template-columns:1fr; }
  .score-summary { display:none; }
  .report-title h2 { max-width:190px; }
}
</style>
