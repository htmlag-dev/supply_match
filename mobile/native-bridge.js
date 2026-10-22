import {Capacitor} from '@capacitor/core';
import {SecureStorage,KeychainAccess} from '@aparajita/capacitor-secure-storage';
import {Browser} from '@capacitor/browser';

if(Capacitor.isNativePlatform()) {
  const storageReady=(async()=>{await SecureStorage.setKeyPrefix('supplymatch_');await SecureStorage.setSynchronize(false);await SecureStorage.setDefaultKeychainAccess(KeychainAccess.whenUnlockedThisDeviceOnly);})();
  window.SupplyMatchNative={
    async getSession(){await storageReady;return SecureStorage.get('session',false,false);},
    async setSession(value){await storageReady;await SecureStorage.set('session',value,false,false,KeychainAccess.whenUnlockedThisDeviceOnly);},
    async clearSession(){await storageReady;await SecureStorage.remove('session',false);},
    subscriptionsAvailable:false,
    async openPayment(url) {
      const parsed=new URL(url);
      if(parsed.protocol!=='https:') throw new Error('Payment URL requires HTTPS');
      await Browser.open({url:parsed.href,toolbarColor:'#254f40'});
    }
  };
  Browser.addListener('browserFinished',()=>window.dispatchEvent(new Event('payments-return')));
}
