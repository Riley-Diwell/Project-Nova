package com.example.novav2.auth

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Keeps the [Session] on disk, encrypted with an AES-GCM key that never leaves Android Keystore.
 *
 * - The key has no user-authentication requirement, so background services (SignalMonitorService,
 *   AssistVoiceService) can read the session with the screen locked.
 * - The ciphertext lives in the private prefs file `nova_auth`, which is excluded from backup
 *   (res/xml/backup_rules.xml, data_extraction_rules.xml). A restored copy couldn't be decrypted
 *   anyway, since the Keystore key doesn't travel with it.
 * - Anything that fails to decrypt is treated as signed out and wiped.
 *
 * Hand-rolled rather than androidx.security's EncryptedSharedPreferences, which is deprecated.
 */
class TokenStore(context: Context) {
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun load(): Session? {
        val blob = prefs.getString(KEY_SESSION, null) ?: return null
        return try {
            val bytes = Base64.decode(blob, Base64.NO_WRAP)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, bytes, 0, IV_BYTES))
            val json = String(cipher.doFinal(bytes, IV_BYTES, bytes.size - IV_BYTES), Charsets.UTF_8)
            Session.fromJson(JSONObject(json))
        } catch (e: Exception) {
            clear()
            null
        }
    }

    /** Written synchronously: a rotated refresh token that isn't saved is a lost session. */
    fun save(session: Session) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val ciphertext = cipher.doFinal(session.toJson().toString().toByteArray(Charsets.UTF_8))
        val blob = Base64.encodeToString(cipher.iv + ciphertext, Base64.NO_WRAP)
        prefs.edit().putString(KEY_SESSION, blob).commit()
    }

    fun clear() {
        prefs.edit().remove(KEY_SESSION).commit()
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    private companion object {
        /** Matches the exclusions in res/xml/backup_rules.xml and data_extraction_rules.xml. */
        const val PREFS_NAME = "nova_auth"
        const val KEY_SESSION = "session"
        const val KEY_ALIAS = "nova_auth_session"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
        const val TAG_BITS = 128
    }
}
