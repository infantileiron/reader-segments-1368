/** @typedef {{id:number,code:string,weekly_reading:number,completion:number,night_share:number,note:string}} Reader */
/** @typedef {{id:number,name:string,k:number,features:string[],state:string,result:object}} Segment */
/** @typedef {{code:string,cluster_id:number,rounds_sampled:number,rounds_co_sampled:number,same_pairs_observed:number,same_pairs_agreed:number,stability:number|null,sufficient_evidence:boolean,stable:boolean,reason:string}} ReviewMember */
/** @typedef {{round:number,seed:number,size:number,codes:string[],status:'ok'|'failed',error:string,assignments:{code:string,cluster_id:number}[]}} ReviewSample */
/** @typedef {{id:number,segment_id:number,state:string,rounds:number,threshold:number,summary:object,samples:ReviewSample[],members:ReviewMember[],digest:string,error:string}} Review */
/** @typedef {{id:number,segment_id:number,cluster_id:number,title:string,note:string,members:string[],status:string,basis?:{review_id:number,review_digest:string,excluded:{code:string,reason:string}[],approved_at:string,approved_digest:string}}} Outreach */
