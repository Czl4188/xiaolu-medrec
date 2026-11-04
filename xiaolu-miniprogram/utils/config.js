/**
 * 后端地址与访问码。
 *
 * 小程序不能直接读网页的 localStorage，账号与档案都走同一个 FastAPI：
 * 登录拿到 token 后，后续请求都带 X-Xiaolu-Token。
 *
 * 上线前注意：微信后台「开发设置 → 服务器域名」要把 apiBase 的域名加进
 * request 合法域名，否则真机会报「不在以下 request 合法域名列表中」。
 */
module.exports = {
    apiBase: 'https://xiaolu-medrec.app.workbuddy.host',
    shareCode: 'C4VL6KLNQG',
    appName: '小鹿医助',
};
