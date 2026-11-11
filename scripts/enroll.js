Java.perform(function () {
  function cls(n) { try { return Java.use(n); } catch (_) { return Java.use('defpackage.' + n); } }
  const ctx = Java.use('com.jyb.comm.base.BaseApplication').baseContext.value;
  const version = String(ctx.getPackageManager().getPackageInfo('com.tsci.hms',0).versionName.value);
  if (version !== '1.0.6') throw new Error('Unsupported BSL app version; inspect the enrollment adapter first.');
  const account = String(cls('eh6').m('HMS'));
  const state = cls('dh6').n('HMS');
  send({
    account: account,
    server: String(cls('pf6').d().h('hms_'+account+'_SERVER_INFO','')),
    login_xml: String(cls('o00').b(state.E())),
    account_number: String(state.h()),
    user_id: String(state.z()),
    device_code: String(cls('nw').c(ctx)),
    device_name: String(cls('nw').d()),
    rsa_modulus: String(ctx.getString(Java.use('com.tsci.module.dataservice.R$string').comm_AES256_client_key.value)),
    app_version: version
  });
});
