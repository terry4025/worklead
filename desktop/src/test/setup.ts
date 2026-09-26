// 테스트 공통 설정: 렌더 결과를 테스트마다 정리한다
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => cleanup());
