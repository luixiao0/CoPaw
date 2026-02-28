import { useState, useEffect, useMemo } from "react";
import { SaveOutlined } from "@ant-design/icons";
import { Select, Button, message, Switch, InputNumber, Input } from "@agentscope-ai/design";
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
    vision?: {
      image?: {
        enabled?: boolean;
        attachments_mode?: string;
        max_images?: number;
        timeout_seconds?: number;
        max_output_chars?: number;
        prompt_override?: string;
      };
      audio?: {
        enabled?: boolean;
        attachments_mode?: string;
        max_items?: number;
        timeout_seconds?: number;
        max_output_chars?: number;
        prompt_override?: string;
      };
      video?: {
        enabled?: boolean;
        attachments_mode?: string;
        max_items?: number;
        timeout_seconds?: number;
        max_output_chars?: number;
        prompt_override?: string;
      };
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
  const [savingVision, setSavingVision] = useState(false);
  const [visionDirty, setVisionDirty] = useState(false);
  const [visionImageEnabled, setVisionImageEnabled] = useState(true);
  const [visionImageMode, setVisionImageMode] = useState<"first" | "all">("first");
  const [visionImageMax, setVisionImageMax] = useState(4);
  const [visionImageTimeout, setVisionImageTimeout] = useState(60);
  const [visionImageMaxChars, setVisionImageMaxChars] = useState(4000);
  const [visionImagePrompt, setVisionImagePrompt] = useState("");
  const [visionAudioEnabled, setVisionAudioEnabled] = useState(false);
  const [visionAudioMode, setVisionAudioMode] = useState<"first" | "all">("first");
  const [visionAudioMax, setVisionAudioMax] = useState(1);
  const [visionAudioTimeout, setVisionAudioTimeout] = useState(90);
  const [visionAudioMaxChars, setVisionAudioMaxChars] = useState(6000);
  const [visionAudioPrompt, setVisionAudioPrompt] = useState("");
  const [visionVideoEnabled, setVisionVideoEnabled] = useState(false);
  const [visionVideoMode, setVisionVideoMode] = useState<"first" | "all">("first");
  const [visionVideoMax, setVisionVideoMax] = useState(1);
  const [visionVideoTimeout, setVisionVideoTimeout] = useState(120);
  const [visionVideoMaxChars, setVisionVideoMaxChars] = useState(6000);
  const [visionVideoPrompt, setVisionVideoPrompt] = useState("");

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

  useEffect(() => {
    const image = activeModels?.vision?.image;
    const audio = activeModels?.vision?.audio;
    const video = activeModels?.vision?.video;
    setVisionImageEnabled(image?.enabled ?? true);
    setVisionImageMode((image?.attachments_mode as "first" | "all") || "first");
    setVisionImageMax(image?.max_images ?? 4);
    setVisionImageTimeout(image?.timeout_seconds ?? 60);
    setVisionImageMaxChars(image?.max_output_chars ?? 4000);
    setVisionImagePrompt(image?.prompt_override ?? "");
    setVisionAudioEnabled(audio?.enabled ?? false);
    setVisionAudioMode((audio?.attachments_mode as "first" | "all") || "first");
    setVisionAudioMax(audio?.max_items ?? 1);
    setVisionAudioTimeout(audio?.timeout_seconds ?? 90);
    setVisionAudioMaxChars(audio?.max_output_chars ?? 6000);
    setVisionAudioPrompt(audio?.prompt_override ?? "");
    setVisionVideoEnabled(video?.enabled ?? false);
    setVisionVideoMode((video?.attachments_mode as "first" | "all") || "first");
    setVisionVideoMax(video?.max_items ?? 1);
    setVisionVideoTimeout(video?.timeout_seconds ?? 120);
    setVisionVideoMaxChars(video?.max_output_chars ?? 6000);
    setVisionVideoPrompt(video?.prompt_override ?? "");
    setVisionDirty(false);
  }, [activeModels?.vision]);

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

  const handleSaveVision = async () => {
    setSavingVision(true);
    try {
      await Promise.all([
        api.setVisionImageSettings({
          enabled: visionImageEnabled,
          attachments_mode: visionImageMode,
          max_images: visionImageMax,
          timeout_seconds: visionImageTimeout,
          max_output_chars: visionImageMaxChars,
          prompt_override: visionImagePrompt,
        }),
        api.setVisionAudioSettings({
          enabled: visionAudioEnabled,
          attachments_mode: visionAudioMode,
          max_items: visionAudioMax,
          timeout_seconds: visionAudioTimeout,
          max_output_chars: visionAudioMaxChars,
          prompt_override: visionAudioPrompt,
        }),
        api.setVisionVideoSettings({
          enabled: visionVideoEnabled,
          attachments_mode: visionVideoMode,
          max_items: visionVideoMax,
          timeout_seconds: visionVideoTimeout,
          max_output_chars: visionVideoMaxChars,
          prompt_override: visionVideoPrompt,
        }),
      ]);
      message.success("Vision settings updated");
      setVisionDirty(false);
      onSaved();
    } catch (error) {
      const errMsg =
        error instanceof Error ? error.message : t("models.failedToSave");
      message.error(errMsg);
    } finally {
      setSavingVision(false);
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

      <div className={styles.slotSection}>
        <div className={styles.slotHeader}>
          <h3 className={styles.slotTitle}>Vision/Media Prepass</h3>
        </div>

        <div className={styles.visionGrid}>
          <div className={styles.visionCard}>
            <div className={styles.visionCardHeader}>
              <span>Image</span>
              <Switch
                checked={visionImageEnabled}
                onChange={(v) => {
                  setVisionImageEnabled(v);
                  setVisionDirty(true);
                }}
              />
            </div>
            <div className={styles.slotForm}>
              <div className={styles.slotField}>
                <label className={styles.slotLabel}>Mode</label>
                <Select
                  value={visionImageMode}
                  onChange={(v) => {
                    setVisionImageMode(v as "first" | "all");
                    setVisionDirty(true);
                  }}
                  options={[
                    { value: "first", label: "first" },
                    { value: "all", label: "all" },
                  ]}
                />
              </div>
              <div className={styles.slotField}>
                <label className={styles.slotLabel}>Max items</label>
                <InputNumber
                  style={{ width: "100%" }}
                  min={1}
                  max={16}
                  value={visionImageMax}
                  onChange={(v) => {
                    setVisionImageMax(Number(v || 1));
                    setVisionDirty(true);
                  }}
                />
              </div>
            </div>
          </div>

          <div className={styles.visionCard}>
            <div className={styles.visionCardHeader}>
              <span>Audio</span>
              <Switch
                checked={visionAudioEnabled}
                onChange={(v) => {
                  setVisionAudioEnabled(v);
                  setVisionDirty(true);
                }}
              />
            </div>
            <div className={styles.slotForm}>
              <div className={styles.slotField}>
                <label className={styles.slotLabel}>Mode</label>
                <Select
                  value={visionAudioMode}
                  onChange={(v) => {
                    setVisionAudioMode(v as "first" | "all");
                    setVisionDirty(true);
                  }}
                  options={[
                    { value: "first", label: "first" },
                    { value: "all", label: "all" },
                  ]}
                />
              </div>
              <div className={styles.slotField}>
                <label className={styles.slotLabel}>Max items</label>
                <InputNumber
                  style={{ width: "100%" }}
                  min={1}
                  max={8}
                  value={visionAudioMax}
                  onChange={(v) => {
                    setVisionAudioMax(Number(v || 1));
                    setVisionDirty(true);
                  }}
                />
              </div>
            </div>
          </div>

          <div className={styles.visionCard}>
            <div className={styles.visionCardHeader}>
              <span>Video</span>
              <Switch
                checked={visionVideoEnabled}
                onChange={(v) => {
                  setVisionVideoEnabled(v);
                  setVisionDirty(true);
                }}
              />
            </div>
            <div className={styles.slotForm}>
              <div className={styles.slotField}>
                <label className={styles.slotLabel}>Mode</label>
                <Select
                  value={visionVideoMode}
                  onChange={(v) => {
                    setVisionVideoMode(v as "first" | "all");
                    setVisionDirty(true);
                  }}
                  options={[
                    { value: "first", label: "first" },
                    { value: "all", label: "all" },
                  ]}
                />
              </div>
              <div className={styles.slotField}>
                <label className={styles.slotLabel}>Max items</label>
                <InputNumber
                  style={{ width: "100%" }}
                  min={1}
                  max={4}
                  value={visionVideoMax}
                  onChange={(v) => {
                    setVisionVideoMax(Number(v || 1));
                    setVisionDirty(true);
                  }}
                />
              </div>
            </div>
          </div>
        </div>

        <div className={styles.visionAdvancedGrid}>
          <div className={styles.slotField}>
            <label className={styles.slotLabel}>Image timeout/max chars</label>
            <div className={styles.visionInline}>
              <InputNumber
                min={5}
                max={600}
                value={visionImageTimeout}
                onChange={(v) => {
                  setVisionImageTimeout(Number(v || 60));
                  setVisionDirty(true);
                }}
              />
              <InputNumber
                min={200}
                max={30000}
                value={visionImageMaxChars}
                onChange={(v) => {
                  setVisionImageMaxChars(Number(v || 4000));
                  setVisionDirty(true);
                }}
              />
            </div>
          </div>
          <div className={styles.slotField}>
            <label className={styles.slotLabel}>Audio timeout/max chars</label>
            <div className={styles.visionInline}>
              <InputNumber
                min={5}
                max={600}
                value={visionAudioTimeout}
                onChange={(v) => {
                  setVisionAudioTimeout(Number(v || 90));
                  setVisionDirty(true);
                }}
              />
              <InputNumber
                min={200}
                max={30000}
                value={visionAudioMaxChars}
                onChange={(v) => {
                  setVisionAudioMaxChars(Number(v || 6000));
                  setVisionDirty(true);
                }}
              />
            </div>
          </div>
          <div className={styles.slotField}>
            <label className={styles.slotLabel}>Video timeout/max chars</label>
            <div className={styles.visionInline}>
              <InputNumber
                min={5}
                max={600}
                value={visionVideoTimeout}
                onChange={(v) => {
                  setVisionVideoTimeout(Number(v || 120));
                  setVisionDirty(true);
                }}
              />
              <InputNumber
                min={200}
                max={30000}
                value={visionVideoMaxChars}
                onChange={(v) => {
                  setVisionVideoMaxChars(Number(v || 6000));
                  setVisionDirty(true);
                }}
              />
            </div>
          </div>
          <div className={styles.slotField}>
            <label className={styles.slotLabel}>Image prompt override</label>
            <Input
              value={visionImagePrompt}
              onChange={(e) => {
                setVisionImagePrompt(e.target.value);
                setVisionDirty(true);
              }}
            />
          </div>
          <div className={styles.slotField}>
            <label className={styles.slotLabel}>Audio prompt override</label>
            <Input
              value={visionAudioPrompt}
              onChange={(e) => {
                setVisionAudioPrompt(e.target.value);
                setVisionDirty(true);
              }}
            />
          </div>
          <div className={styles.slotField}>
            <label className={styles.slotLabel}>Video prompt override</label>
            <Input
              value={visionVideoPrompt}
              onChange={(e) => {
                setVisionVideoPrompt(e.target.value);
                setVisionDirty(true);
              }}
            />
          </div>
        </div>

        <div className={styles.slotActions}>
          <Button
            type="primary"
            loading={savingVision}
            disabled={!visionDirty}
            onClick={handleSaveVision}
            icon={<SaveOutlined />}
          >
            {t("models.save")}
          </Button>
        </div>
      </div>
    </>
  );
}
