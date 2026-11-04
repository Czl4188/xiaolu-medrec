const { readUser } = require('./utils/api');

App({
    globalData: {
        appName: '小鹿医助',
        user: null,
    },

    onLaunch() {
        this.globalData.user = readUser();
    },
});
