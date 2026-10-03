package com.example.novav2.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.novav2.data.ReminderEntity
import com.example.novav2.model.ReminderPriority
import com.example.novav2.model.ReminderRecurrence
import com.example.novav2.state.ReminderNotifier
import com.example.novav2.state.ReminderRepository
import com.example.novav2.state.GeofenceRegistrar
import com.example.novav2.state.ReminderScheduler
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import java.time.LocalDateTime

/**
 * The Reminders tab. Observes the table rather than loading it once (the ChatViewModel
 * pattern) - a reminder set by voice from the wearable, or ticked off from a notification,
 * lands here without a refresh. Every change goes through [ReminderRepository], the table's only
 * writer, so the alarm is always reconciled.
 */
class RemindersViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application

    val reminders: StateFlow<List<ReminderEntity>> = ReminderRepository.observeAll(app)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    val exactAlarmsAllowed: StateFlow<Boolean> = ReminderScheduler.exactAlarmsAllowed

    /** Whether place reminders can go off - the Reminders screen's location banner. */
    val placeStatus: StateFlow<GeofenceRegistrar.Status> = GeofenceRegistrar.status

    private val _notificationsAllowed = MutableStateFlow(true)
    val notificationsAllowed: StateFlow<Boolean> = _notificationsAllowed

    /** Re-read on every return to the screen - all can be changed in system Settings. The
     * reconcile re-registers geofences if location was just allowed. */
    fun refreshPermissions() {
        _notificationsAllowed.value = ReminderNotifier.canPost(app)
        viewModelScope.launch { ReminderScheduler.reconcile(app) }
    }

    fun complete(id: String) = viewModelScope.launch { ReminderRepository.complete(app, id) }

    fun delete(id: String) = viewModelScope.launch { ReminderRepository.cancel(app, id) }

    fun undoDelete(id: String) = viewModelScope.launch { ReminderRepository.undoCancel(app, id) }

    fun clearDone(id: String) = viewModelScope.launch { ReminderRepository.clearDone(app, id) }

    fun undoClear(id: String) = viewModelScope.launch { ReminderRepository.unclearDone(app, id) }

    /** Creates a reminder when [id] is null, otherwise edits it - [due] null on an edit means
     * the time wasn't touched. [onResult] gets false if a new time has already passed. */
    fun save(
        id: String?,
        text: String,
        due: LocalDateTime?,
        priority: ReminderPriority,
        recurrence: ReminderRecurrence?,
        onResult: (Boolean) -> Unit,
    ) = viewModelScope.launch {
        if (due != null && due.isBefore(LocalDateTime.now().minusMinutes(1))) {
            onResult(false)
            return@launch
        }
        val saved = if (id == null) {
            due?.let { ReminderRepository.create(app, text, it, priority, recurrence = recurrence) }
        } else {
            ReminderRepository.edit(
                app, id, text = text, due = due, priority = priority,
                recurrence = recurrence, clearRecurrence = recurrence == null,
            )
        }
        onResult(saved != null)
    }
}
