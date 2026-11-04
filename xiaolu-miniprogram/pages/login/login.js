const { login, register } = require('../../utils/api');

const PHONE_RE = /^1[3-9]\d{9}$/;

Page({
    data: {
        mode: 'login',
        phone: '',
        password: '',
        username: '',
        error: '',
        busy: false,
    },

    onLoad() {
        // 从首页点进来时保持默认登录态
        this.setData({ mode: 'login' });
    },

    switchToLogin() {
        this.setData({ mode: 'login', error: '' });
    },

    switchToRegister() {
        this.setData({ mode: 'register', error: '' });
    },

    onInput(e) {
        const field = e.currentTarget.dataset.field;
        const patch = {};
        patch[field] = e.detail.value;
        patch.error = '';
        this.setData(patch);
    },

    submit() {
        if (this.data.busy) {
            return;
        }
        const phone = (this.data.phone || '').replace(/\s|-/g, '');
        const password = this.data.password || '';
        const username = (this.data.username || '').trim();
        const isRegister = this.data.mode === 'register';

        if (!PHONE_RE.test(phone)) {
            this.setData({ error: '手机号要填 11 位，1 开头' });
            return;
        }
        if (password.length < 8) {
            this.setData({ error: '密码至少 8 位' });
            return;
        }
        if (isRegister && username.length < 2) {
            this.setData({ error: '用户名至少 2 个字' });
            return;
        }

        this.setData({ busy: true, error: '' });
        const task = isRegister ? register(username, phone, password) : login(phone, password);
        task.then(() => {
            this.setData({ busy: false });
            wx.reLaunch({ url: '/pages/index/index' });
        }).catch((err) => {
            const status = err.status;
            let msg = err.message || '登录失败';
            if (status === 409 && isRegister) {
                msg = '这个手机号已经注册过了，直接登录就行';
            } else if (status === 401 && !isRegister) {
                msg = '手机号或密码不对';
            } else if (status === 422) {
                msg = '格式不对：手机号 11 位，密码至少 8 位';
            }
            this.setData({ busy: false, error: msg });
        });
    },

    goHelp() {
        wx.showModal({
            title: '登录说明',
            content: '用手机号 + 密码登录。没注册过先切到「创建新账号」，用户名可以重名，手机号是账号。',
            showCancel: false,
            confirmText: '知道了',
        });
    },
});
