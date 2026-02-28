import { useState, useEffect, useMemo } from "react";
import { SaveOutlined } from "@ant-design/icons";
import { Select, Button, message } from "@agentscope-ai/design";
import type { ModelSlotRequest } from "../../../../../api/types";
import api from "../../../../../api";
import { useTranslation } from "react-i18next";
import styles from "../../index.module.less";

interface ModelsSectionProps {
  providers: Array<{
    id: string;
    name: string;
    models?: Array<{ id: string; name: string }>;
    extra_models?: Array<{ id: string; name: string }>;
    current_base_url?: string;
    has_api_key: boolean;
    is_custom: boolean;
    is_local?: boolean;
  }>;
  activeModels: {
    active_llm?: {
      provider_id?: string;
      model?: string;
    };
    active_vlm?: {
      provider_id?: string;
      model?: string;
    };
  } | null;
  onSaved: () => void;
}

export function ModelsSection({
  providers,
  activeModels,
  onSaved,
}: ModelsSectionProps) {
  const { t } = useTranslation();
  const [savingLlm, setSavingLlm] = useState(false);
  const [savingVlm, setSavingVlm] = useState(false);
  const [selectedLlmProviderId, setSelectedLlmProviderId] = useState<
    string | undefined
  >(undefined);
  const [selectedLlmModel, setSelectedLlmModel] = useState<string | undefined>(
    undefined,
  );
  const [selectedVlmProviderId, setSelectedVlmProviderId] = useState<
    string | undefined
  >(undefined);
  const [selectedVlmModel, setSelectedVlmModel] = useState<string | undefined>(
    undefined,
  );
  const [llmDirty, setLlmDirty] = useState(false);
  const [vlmDirty, setVlmDirty] = useState(false);

  const currentLlmSlot = activeModels?.active_llm;
  const currentVlmSlot = activeModels?.active_vlm;

  const eligible = useMemo(
    () =>
      providers.filter((p) => {
        if (p.is_local) return (p.models?.length ?? 0) > 0;
        return p.is_custom ? !!p.current_base_url : p.has_api_key;
      }),
    [providers],
  );

  useEffect(() => {
    if (currentLlmSlot) {
      setSelectedLlmProviderId(currentLlmSlot.provider_id || undefined);
      setSelectedLlmModel(currentLlmSlot.model || undefined);
    }
    setLlmDirty(false);
  }, [currentLlmSlot?.provider_id, currentLlmSlot?.model]);

  useEffect(() => {
    if (currentVlmSlot) {
      setSelectedVlmProviderId(currentVlmSlot.provider_id || undefined);
      setSelectedVlmModel(currentVlmSlot.model || undefined);
    }
    setVlmDirty(false);
  }, [currentVlmSlot?.provider_id, currentVlmSlot?.model]);

  const llmProvider = providers.find((p) => p.id === selectedLlmProviderId);
  const llmModelOptions = llmProvider?.models ?? [];
  const hasLlmModels = llmModelOptions.length > 0;

  const vlmProvider = providers.find((p) => p.id === selectedVlmProviderId);
  const vlmModelOptions = vlmProvider?.models ?? [];
  const hasVlmModels = vlmModelOptions.length > 0;

  const handleLlmProviderChange = (pid: string) => {
    setSelectedLlmProviderId(pid);
    setSelectedLlmModel(undefined);
    setLlmDirty(true);
  };

  const handleLlmModelChange = (model: string) => {
    setSelectedLlmModel(model);
    setLlmDirty(true);
  };

  const handleVlmProviderChange = (pid: string) => {
    setSelectedVlmProviderId(pid);
    setSelectedVlmModel(undefined);
    setVlmDirty(true);
  };

  const handleVlmModelChange = (model: string) => {
    setSelectedVlmModel(model);
    setVlmDirty(true);
  };

  const handleSaveLlm = async () => {
    if (!selectedLlmProviderId || !selectedLlmModel) return;

    const body: ModelSlotRequest = {
      provider_id: selectedLlmProviderId,
      model: selectedLlmModel,
    };

    setSavingLlm(true);
    try {
      await api.setActiveLlm(body);
      message.success(t("models.llmModelUpdated"));
      setLlmDirty(false);
      onSaved();
    } catch (error) {
      const errMsg =
        error instanceof Error ? error.message : t("models.failedToSave");
      message.error(errMsg);
    } finally {
      setSavingLlm(false);
    }
  };

  const handleSaveVlm = async () => {
    if (!selectedVlmProviderId || !selectedVlmModel) return;

    const body: ModelSlotRequest = {
      provider_id: selectedVlmProviderId,
      model: selectedVlmModel,
    };

    setSavingVlm(true);
    try {
      await api.setActiveVlm(body);
      message.success("VLM model updated");
      setVlmDirty(false);
      onSaved();
    } catch (error) {
      const errMsg =
        error instanceof Error ? error.message : t("models.failedToSave");
      message.error(errMsg);
    } finally {
      setSavingVlm(false);
    }
  };

  const llmActive =
    currentLlmSlot &&
    currentLlmSlot.provider_id === selectedLlmProviderId &&
    currentLlmSlot.model === selectedLlmModel;
  const canSaveLlm = llmDirty && !!selectedLlmProviderId && !!selectedLlmModel;

  const vlmActive =
    currentVlmSlot &&
    currentVlmSlot.provider_id === selectedVlmProviderId &&
    currentVlmSlot.model === selectedVlmModel;
  const canSaveVlm = vlmDirty && !!selectedVlmProviderId && !!selectedVlmModel;

  return (
    <>
      <div className={styles.slotSection}>
        <div className={styles.slotHeader}>
          <h3 className={styles.slotTitle}>{t("models.llmConfiguration")}</h3>
          {currentLlmSlot?.provider_id && currentLlmSlot?.model && (
            <span className={styles.slotCurrent}>
              {t("models.active", {
                provider: currentLlmSlot.provider_id,
                model: currentLlmSlot.model,
              })}
            </span>
          )}
        </div>

        <div className={styles.slotForm}>
          <div className={styles.slotField}>
            <label className={styles.slotLabel}>{t("models.provider")}</label>
            <Select
              style={{ width: "100%" }}
              placeholder={t("models.selectProvider")}
              value={selectedLlmProviderId}
              onChange={handleLlmProviderChange}
              options={eligible.map((p) => ({
                value: p.id,
                label: p.name,
              }))}
            />
          </div>

          <div className={styles.slotField}>
            <label className={styles.slotLabel}>{t("models.model")}</label>
            <Select
              style={{ width: "100%" }}
              placeholder={
                hasLlmModels ? t("models.selectModel") : t("models.addModelFirst")
              }
              disabled={!hasLlmModels}
              showSearch
              optionFilterProp="label"
              value={selectedLlmModel}
              onChange={handleLlmModelChange}
              options={llmModelOptions.map((m) => ({
                value: m.id,
                label: `${m.name} (${m.id})`,
              }))}
            />
          </div>

          <div
            className={styles.slotField}
            style={{ flex: "0 0 auto", minWidth: "120px" }}
          >
            <label className={styles.slotLabel} style={{ visibility: "hidden" }}>
              {t("models.actions")}
            </label>
            <Button
              type="primary"
              loading={savingLlm}
              disabled={!canSaveLlm}
              onClick={handleSaveLlm}
              block
              icon={<SaveOutlined />}
            >
              {llmActive ? t("models.saved") : t("models.save")}
            </Button>
          </div>
        </div>
      </div>

      <div className={styles.slotSection}>
        <div className={styles.slotHeader}>
          <h3 className={styles.slotTitle}>VLM Configuration</h3>
          {currentVlmSlot?.provider_id && currentVlmSlot?.model && (
            <span className={styles.slotCurrent}>
              {t("models.active", {
                provider: currentVlmSlot.provider_id,
                model: currentVlmSlot.model,
              })}
            </span>
          )}
        </div>

        <div className={styles.slotForm}>
          <div className={styles.slotField}>
            <label className={styles.slotLabel}>{t("models.provider")}</label>
            <Select
              style={{ width: "100%" }}
              placeholder={t("models.selectProvider")}
              value={selectedVlmProviderId}
              onChange={handleVlmProviderChange}
              options={eligible.map((p) => ({
                value: p.id,
                label: p.name,
              }))}
            />
          </div>

          <div className={styles.slotField}>
            <label className={styles.slotLabel}>{t("models.model")}</label>
            <Select
              style={{ width: "100%" }}
              placeholder={
                hasVlmModels ? t("models.selectModel") : t("models.addModelFirst")
              }
              disabled={!hasVlmModels}
              showSearch
              optionFilterProp="label"
              value={selectedVlmModel}
              onChange={handleVlmModelChange}
              options={vlmModelOptions.map((m) => ({
                value: m.id,
                label: `${m.name} (${m.id})`,
              }))}
            />
          </div>

          <div
            className={styles.slotField}
            style={{ flex: "0 0 auto", minWidth: "120px" }}
          >
            <label className={styles.slotLabel} style={{ visibility: "hidden" }}>
              {t("models.actions")}
            </label>
            <Button
              type="primary"
              loading={savingVlm}
              disabled={!canSaveVlm}
              onClick={handleSaveVlm}
              block
              icon={<SaveOutlined />}
            >
              {vlmActive ? t("models.saved") : t("models.save")}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
