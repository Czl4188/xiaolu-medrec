const { request, readToken, readUser, logout } = require('../../utils/api');
const { maskPhone } = require('../../utils/format');

Page({
    data: {
        logged: false,
        name: '',
        phone: '',
        phoneBound: false,
        total: 0,
        error: '',
        version: 'v1.0.0 · 2026-11-04',
    },

    onShow() {
        if (!readToken()) {
            this.setData({ logged: false, name: '', phone: '', total: 0, error: '' });
            return;
        }
        const cached = readUser() || {};
        this.setData({
            logged: true,
            name: cached.username || '',
            phone: maskPhone(cached.phone || ''),
            phoneBound: !!cached.phone_bound,
        });
        this.load();
    },

    load() {
        request('/api/me')
            .then((me) => {
                this.setData({
                    name: me.username || this.data.name,
                    phone: maskPhone(me.phone || ''),
                    phoneBound: !!me.phone_bound,
                });
            })
            .catch(() => {});

        request('/api/records')
            .then((list) => {
                this.setData({ total: (list || []).length, error: '' });
            })
            .catch((err) => {
                this.setData({ error: err.message || '读取失败' });
            });
    },

    goLogin() {
        wx.navigateTo({ url: '/pages/login/login' });
    },

    showAbout() {
        wx.showModal({
            title: '关于小鹿医助',
            content: '小鹿把你每次问诊整理成可翻回去的档案，并只按档案回答。\n\n内容不构成诊疗建议，用药请遵医嘱。录音与整理请使用网页版。',
            showCancel: false,
            confirmText: '知道了',
        });
    },

    showPrivacy() {
        wx.showModal({
            title: '数据说明',
            content: '账号、档案与个人资料保存在服务端，换设备登录可恢复。手机号就是登录账号。',
            showCancel: false,
            confirmText: '知道了',
        });
    },

    doLogout() {
        wx.showModal({
            title: '退出登录',
            content: '退出后需要重新用手机号登录，档案不会删除。',
            confirmText: '退出',
            cancelText: '取消',
            success: (res) => {
                if (!res.confirm) {
                    return;
                }
                logout();
                this.setData({ logged: false, name: '', phone: '', total: 0 });
                wx.reLaunch({ url: '/pages/index/index' });
            },
        });
    },
});
